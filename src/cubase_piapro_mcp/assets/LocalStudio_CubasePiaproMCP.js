// ES5 driver for Cubase LE 14. Control ports are separate from note input.
var api = require('midiremote_api_v1');
var driver = api.makeDeviceDriver('LocalStudio', 'CubasePiaproMCP', 'LocalStudio');
var input = driver.mPorts.makeMidiInput('CP MCP To Cubase');
var output = driver.mPorts.makeMidiOutput('CP MCP From Cubase');
driver.makeDetectionUnit().detectPortPair(input, output)
    .expectInputNameContains('CP MCP To Cubase')
    .expectOutputNameContains('CP MCP From Cubase');
var page = driver.mMapping.makePage('Data-first MCP');
var active = false;
var session = '';
var cache = {playing:127, recording:127, volume:127, pan:127, mute:127, solo:127};
function bind(name, cc, host, button) {
    var control = button ? driver.mSurface.makeButton(cc, 0, 1, 1) : driver.mSurface.makeKnob(cc, 1, 1, 1);
    control.mSurfaceValue.mMidiBinding.setInputPort(input).bindToControlChange(15, cc);
    var commands = {playing:'Start', recording:'Record', stopTrigger:'Stop'};
    if (commands[name]) {
        page.makeCommandBinding(control.mSurfaceValue, 'Transport', commands[name]).filterByValue(1);
        page.makeValueBinding(driver.mSurface.makeCustomValueVariable(name + ' host feedback'), host);
    } else {
        page.makeValueBinding(control.mSurfaceValue, host).setTypeDefault().setValueTakeOverModeJump();
    }
    host.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
        cache[name] = Math.max(0, Math.min(126, Math.round(value * 126)));
    };
}
var transport = page.mHostAccess.mTransport.mValue;
var channel = page.mHostAccess.mTrackSelection.mMixerChannel;
bind('playing', 20, transport.mStart, true);
bind('stopTrigger', 21, transport.mStop, true);
bind('recording', 22, transport.mRecord, true);
bind('volume', 30, channel.mValue.mVolume, false);
bind('pan', 31, channel.mValue.mPan, false);
bind('mute', 32, channel.mValue.mMute, true);
bind('solo', 33, channel.mValue.mSolo, true);
var query = driver.mSurface.makeCustomValueVariable('MCP state query');
query.mMidiBinding.setInputPort(input).bindToControlChange(15, 119);
query.mOnProcessValueChange = function (activeDevice, value) {
    if (!active) { return; }
    var nonce = Math.round(value * 127);
    output.sendMidi(activeDevice, [240,125,67,80,2,nonce,cache.playing,cache.recording,
        cache.volume,cache.pan,cache.mute,cache.solo,247]);
};

// Query callbacks never dereference native host handles. Only populated host
// callbacks read IDs, using the mapping context passed to that same callback.
function hostID(host, activeMapping, title) {
    if (!title || !activeMapping) { return ''; }
    try {
        var uid = host.getUniqueIDString(activeMapping);
        return typeof uid === 'string' ? uid : '';
    } catch (error) { return ''; }
}
function clearEntry(entry) {
    entry.name = '';
    entry.uid = '';
    entry.plugin = '';
    entry.selected = false;
}
function cacheIdentity(host, entry) {
    host.mOnTitleChange = function (activeDevice, activeMapping, title) {
        if (!title) { clearEntry(entry); return; }
        entry.name = title;
        entry.uid = hostID(host, activeMapping, title);
    };
    host.mOnColorChange = function (activeDevice, activeMapping, r, g, b, a, isActive) {
        if (!isActive) { clearEntry(entry); return; }
        if (entry.name) { entry.uid = hostID(host, activeMapping, entry.name); }
    };
}
var selected = {name:'', uid:'', plugin:'', selected:false};
cacheIdentity(channel, selected);
channel.mInstrumentPluginSlot.mOnChangePluginIdentity = function (activeDevice, activeMapping, name) {
    selected.plugin = name;
};
var editorOpen = 127;
var openEditor = driver.mSurface.makeCustomValueVariable('MCP open instrument editor');
openEditor.mMidiBinding.setInputPort(input).bindToControlChange(15, 115);
page.makeValueBinding(openEditor, channel.mValue.mInstrumentOpen).filterByValue(1);
channel.mValue.mInstrumentOpen.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
    editorOpen = Math.round(value);
};

var bank = page.mHostAccess.mMixConsole.makeMixerBankZone('MCP track inventory');
bank.includeAudioChannels().includeInstrumentChannels().includeSamplerChannels().includeMIDIChannels();
bank.setFollowVisibility(false);
var trackCache = [];
function setupTrack(slot) {
    var host = bank.makeMixerBankChannel();
    var entry = {slot:slot, name:'', uid:'', selected:false, plugin:''};
    trackCache.push(entry);
    cacheIdentity(host, entry);
    var selector = driver.mSurface.makeCustomValueVariable('MCP track ' + slot);
    if (slot < 64) { selector.mMidiBinding.setInputPort(input).bindToControlChange(14, slot); }
    page.makeValueBinding(selector, host.mValue.mSelected).filterByValue(1);
    host.mValue.mSelected.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
        entry.selected = value > 0.5;
    };
    page.makeValueBinding(driver.mSurface.makeCustomValueVariable('MCP plugin ' + slot), host.mInstrumentPluginSlot.mOn);
    host.mInstrumentPluginSlot.mOnChangePluginIdentity = function (activeDevice, activeMapping, name) {
        entry.plugin = name;
    };
}
for (var slot = 0; slot < 65; ++slot) { setupTrack(slot); }

// MIDI tracks expose the destination's plugin identity too. These filtered
// banks cache owner/type membership without ever reading empty native slots.
var typeCache = {instrument:[], midi:[]};
function setupTypeBank(kind) {
    var zone = page.mHostAccess.mMixConsole.makeMixerBankZone('MCP ' + kind + ' identity');
    if (kind === 'instrument') { zone.includeInstrumentChannels(); }
    else { zone.includeMIDIChannels(); }
    zone.setFollowVisibility(false);
    for (var index = 0; index < 64; ++index) {
        var entry = {name:'', uid:''};
        typeCache[kind].push(entry);
        cacheIdentity(zone.makeMixerBankChannel(), entry);
    }
}
setupTypeBank('instrument');
setupTypeBank('midi');
function trackKind(uid) {
    if (!uid) { return 'unknown'; }
    for (var kind in typeCache) {
        for (var index = 0; index < typeCache[kind].length; ++index) {
            if (typeCache[kind][index].uid === uid) { return kind; }
        }
    }
    return 'audio_or_sampler';
}
var resetBank = driver.mSurface.makeCustomValueVariable('MCP reset inventory bank');
resetBank.mMidiBinding.setInputPort(input).bindToControlChange(15, 117);
page.makeActionBinding(resetBank, bank.mAction.mResetBank).filterByValue(1);
var commands = [
    [100, 'AddTrack', 'Audio'], [101, 'AddTrack', 'Instrument'],
    [102, 'AddTrack', 'MIDI'], [104, 'File', 'Save'], [105, 'File', 'Save As']
];
for (var commandIndex = 0; commandIndex < commands.length; ++commandIndex) {
    var spec = commands[commandIndex];
    var trigger = driver.mSurface.makeCustomValueVariable('MCP ' + spec[1] + ' ' + spec[2]);
    trigger.mMidiBinding.setInputPort(input).bindToControlChange(15, spec[0]);
    page.makeCommandBinding(trigger, spec[1], spec[2]).filterByValue(1);
}
page.mOnActivate = function () {
    active = true;
    session = String(new Date().getTime()) + '-' + String(Math.random());
};
page.mOnDeactivate = function () {
    active = false;
    clearEntry(selected);
    editorOpen = 127;
    for (var index = 0; index < trackCache.length; ++index) { clearEntry(trackCache[index]); }
    for (var kind in typeCache) {
        for (var item = 0; item < typeCache[kind].length; ++item) { clearEntry(typeCache[kind][item]); }
    }
};
var versionQuery = driver.mSurface.makeCustomValueVariable('MCP inventory version query');
versionQuery.mMidiBinding.setInputPort(input).bindToControlChange(15, 116);
versionQuery.mOnProcessValueChange = function (activeDevice, value) {
    var nonce = Math.round(value * 127);
    if (nonce) { output.sendMidi(activeDevice, [240,125,67,80,5,nonce,1,247]); }
};
var inventoryQuery = driver.mSurface.makeCustomValueVariable('MCP inventory query');
inventoryQuery.mMidiBinding.setInputPort(input).bindToControlChange(15, 118);
inventoryQuery.mOnProcessValueChange = function (activeDevice, value) {
    var nonce = Math.round(value * 127);
    if (!nonce) { return; }
    if (!active) {
        output.sendMidi(activeDevice, [240,125,67,80,4,nonce,1,247]);
        return;
    }
    var tracks = [];
    for (var index = 0; index < 64; ++index) {
        var entry = trackCache[index];
        if (entry.name) {
            tracks.push({slot:index, name:entry.name, uid:entry.uid, kind:trackKind(entry.uid),
                selected:entry.selected, plugin:entry.plugin});
        }
    }
    var snapshot = {session:session, capacity:64, overflow:!!trackCache[64].name, driver_build:'host-cache-1',
        selected:{name:selected.name, uid:selected.uid, plugin:selected.plugin, editor_open:editorOpen}, tracks:tracks};
    var text = JSON.stringify(snapshot);
    var parts = Math.ceil(text.length / 200);
    if (parts > 127) { return; }
    for (var part = 0; part < parts; ++part) {
        var message = [240,125,67,80,3,nonce,part,parts];
        var end = Math.min(text.length, (part + 1) * 200);
        for (var position = part * 200; position < end; ++position) {
            var code = text.charCodeAt(position);
            message.push(code & 127, (code >> 7) & 127, (code >> 14) & 3);
        }
        message.push(247);
        output.sendMidi(activeDevice, message);
    }
};
