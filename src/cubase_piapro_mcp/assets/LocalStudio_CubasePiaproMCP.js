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
var inventoryGeneration = 0;
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
    if (entry.slot !== undefined && (entry.name || entry.uid || entry.plugin)) { inventoryGeneration += 1; }
    entry.name = '';
    entry.uid = '';
    entry.plugin = '';
    entry.selected = false;
}
function cacheIdentity(host, entry) {
    host.mOnTitleChange = function (activeDevice, activeMapping, title) {
        if (!title) { clearEntry(entry); return; }
        var previousName = entry.name, previousUID = entry.uid;
        entry.name = title;
        entry.uid = hostID(host, activeMapping, title);
        if (entry.slot !== undefined && (entry.name !== previousName || entry.uid !== previousUID)) { inventoryGeneration += 1; }
    };
    host.mOnColorChange = function (activeDevice, activeMapping, r, g, b, a, isActive) {
        if (!isActive) { clearEntry(entry); return; }
        if (entry.name) {
            var previousUID = entry.uid;
            entry.uid = hostID(host, activeMapping, entry.name);
            if (entry.slot !== undefined && entry.uid !== previousUID) { inventoryGeneration += 1; }
        }
    };
}
var selected = {name:'', uid:'', plugin:'', selected:false};
cacheIdentity(channel, selected);
function selectedPluginIdentity(activeDevice, activeMapping, name) {
    selected.plugin = name;
}
channel.mInstrumentPluginSlot.mOnChangePluginIdentity = selectedPluginIdentity;
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
        if (entry.plugin !== name) { inventoryGeneration += 1; }
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
    resetControls();
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
    var snapshot = {session:session, capacity:64, generation:inventoryGeneration, overflow:!!trackCache[64].name, driver_build:'host-cache-1',
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

// Additional controls use ordinary host value bindings. No native object tree
// traversal or UID lookup is performed by these MIDI request callbacks.
var controls = {build:'controls-1', values:{}, plugins:{}, pending:null, group:'summary',
    revision:0, changed:0, outcomes:{}, outcomeOrder:[]};
function updateControl(entry, key, value) {
    if (entry[key] !== value) {
        entry[key] = value;
        controls.revision += 1;
        controls.changed = new Date().getTime();
    }
}
function resetControls() {
    if (!controls) { return; }
    controls.pending = null;
    for (var id in controls.values) {
        var entry = controls.values[id];
        entry.value = null;
        entry.title = '';
        entry.object_title = '';
        entry.display = '';
        entry.active = null;
    }
    for (var key in controls.plugins) {
        var plugin = controls.plugins[key];
        plugin.name = ''; plugin.vendor = ''; plugin.version = ''; plugin.format = ''; plugin.title = '';
    }
    controls.revision += 1;
    controls.changed = new Date().getTime();
}
function controlValue(id, host, group, binary) {
    if (!host) { return; }
    var surface = driver.mSurface.makeCustomValueVariable('MCP control ' + id);
    page.makeValueBinding(surface, host).setTypeDefault().setValueTakeOverModeJump();
    var entry = {id:id, group:group, binary:!!binary, title:'', object_title:'',
        display:'', units:'', value:null, feedback:0, active:null};
    controls.values[id] = entry;
    host.mOnProcessValueChange = function (activeDevice, activeMapping, value) {
        updateControl(entry, 'value', typeof value === 'number' && isFinite(value) ? value : null);
        entry.feedback += 1;
    };
    host.mOnDisplayValueChange = function (activeDevice, activeMapping, text, units) {
        updateControl(entry, 'display', text || '');
        updateControl(entry, 'units', units || '');
    };
    host.mOnTitleChange = function (activeDevice, activeMapping, objectTitle, valueTitle) {
        updateControl(entry, 'object_title', objectTitle || '');
        updateControl(entry, 'title', valueTitle || '');
    };
    host.mOnColorChange = function (activeDevice, activeMapping, r, g, b, a, isActive) {
        updateControl(entry, 'active', !!isActive);
    };
    entry.surface = surface;
}
function pluginControls(key, slot, parameterCount, previous) {
    var entry = {name:'', vendor:'', version:'', format:'', title:''};
    controls.plugins[key] = entry;
    slot.mOnChangePluginIdentity = function (activeDevice, activeMapping, name, vendor, version, format) {
        if (previous) { previous(activeDevice, activeMapping, name, vendor, version, format); }
        updateControl(entry, 'name', name || '');
        updateControl(entry, 'vendor', vendor || '');
        updateControl(entry, 'version', version || '');
        updateControl(entry, 'format', format || '');
    };
    slot.mOnTitleChange = function (activeDevice, activeMapping, title) { entry.title = title || ''; };
    controlValue(key + '.on', slot.mOn, key, true);
    controlValue(key + '.bypass', slot.mBypass, key, true);
    controlValue(key + '.automation_write', slot.mAutomationWrite, key, true);
    if (slot.mParameterBankZone) {
        slot.mParameterBankZone.setWrapAround(false);
        for (var i = 0; i < parameterCount; ++i) {
            controlValue(key + '.parameter.' + i, slot.mParameterBankZone.makeParameterValue(), key, false);
        }
    }
}
if (channel.mChannelEQ && channel.mSends && channel.mInstrumentPluginSlot.mParameterBankZone) {
    controlValue('channel.automation_write', channel.mValue.mAutomationWrite, 'summary', true);
    for (var eqIndex = 1; eqIndex <= 4; ++eqIndex) {
        var eq = channel.mChannelEQ['mBand' + eqIndex];
        controlValue('eq.' + eqIndex + '.gain', eq.mGain, 'eq', false);
        controlValue('eq.' + eqIndex + '.frequency', eq.mFreq, 'eq', false);
        controlValue('eq.' + eqIndex + '.q', eq.mQ, 'eq', false);
        controlValue('eq.' + eqIndex + '.on', eq.mOn, 'eq', true);
        controlValue('eq.' + eqIndex + '.filter', eq.mFilterType, 'eq', false);
    }
    var sendCount = Math.min(8, api.mDefaults.getNumberOfSendSlots());
    for (var sendIndex = 0; sendIndex < sendCount; ++sendIndex) {
        var send = channel.mSends.getByIndex(sendIndex);
        controlValue('send.' + sendIndex + '.level', send.mLevel, 'sends', false);
        controlValue('send.' + sendIndex + '.on', send.mOn, 'sends', true);
        controlValue('send.' + sendIndex + '.pre', send.mPrePost, 'sends', true);
    }
    pluginControls('instrument', channel.mInstrumentPluginSlot, 65, selectedPluginIdentity);
    for (var insertIndex = 0; insertIndex < 4; ++insertIndex) {
        var insert = channel.mInsertAndStripEffects.makeInsertEffectViewer('MCP insert ' + insertIndex);
        insert.accessSlotAtIndex(insertIndex);
        pluginControls('insert.' + insertIndex, insert, 65);
    }
}
function sendControls(activeDevice, nonce, result) {
    var text = JSON.stringify(result);
    var parts = Math.ceil(text.length / 200);
    if (parts > 127) { sendControls(activeDevice, nonce, {error:'CONTROL_RESPONSE_TOO_LARGE'}); return; }
    for (var part = 0; part < parts; ++part) {
        var message = [240,125,67,80,6,nonce,part,parts];
        var end = Math.min(text.length, (part + 1) * 200);
        for (var position = part * 200; position < end; ++position) {
            var code = text.charCodeAt(position);
            message.push(code & 127, (code >> 7) & 127, (code >> 14) & 3);
        }
        message.push(247);
        output.sendMidi(activeDevice, message);
    }
}
function controlsSnapshot(group) {
    group = group || controls.group;
    var values = [];
    for (var id in controls.values) {
        var entry = controls.values[id];
        var parameter = id.indexOf('.parameter.') >= 0;
        if (entry.group !== group || (parameter && !entry.title)) { continue; }
        if (entry.title || entry.object_title || entry.value !== null) {
            values.push({id:id, group:entry.group, binary:entry.binary, title:entry.title,
                object_title:entry.object_title, display:entry.display, units:entry.units,
                value:entry.value, active:entry.active, feedback:entry.feedback});
        }
    }
    return {build:controls.build, group:group, session:session, active:active,
        revision:controls.revision, quiet_ms:new Date().getTime() - controls.changed,
        automation_write:controls.values['channel.automation_write'] ? controls.values['channel.automation_write'].value : null,
        selected:{uid:selected.uid, name:selected.name}, plugins:controls.plugins, values:values};
}
var controlsGroup = driver.mSurface.makeCustomValueVariable('MCP controls group');
controlsGroup.mMidiBinding.setInputPort(input).bindToControlChange(15, 113);
controlsGroup.mOnProcessValueChange = function (activeDevice, value) {
    var groups = ['summary','eq','sends','instrument','insert.0','insert.1','insert.2','insert.3'];
    controls.group = groups[Math.round(value * 127)] || 'summary';
};
var controlsQuery = driver.mSurface.makeCustomValueVariable('MCP controls query');
controlsQuery.mMidiBinding.setInputPort(input).bindToControlChange(15, 114);
controlsQuery.mOnProcessValueChange = function (activeDevice, value) {
    var nonce = Math.round(value * 127);
    if (nonce) { sendControls(activeDevice, nonce, controlsSnapshot()); }
};

function controlFailure(code) { return {dispatched:false, code:code}; }
function executeControls(activeDevice, request) {
    if (!active || !selected.uid || request.session !== session || request.uid !== selected.uid) {
        return controlFailure('CONTROL_TARGET_CHANGED');
    }
    if (request.revision !== controls.revision) { return controlFailure('CONTROL_STATE_CHANGED'); }
    if (cache.playing !== 0 || cache.recording !== 0) { return controlFailure('TRANSPORT_NOT_STOPPED'); }
    if (!controls.values['channel.automation_write'] || controls.values['channel.automation_write'].value !== 0) {
        return controlFailure('AUTOMATION_WRITE_ENABLED');
    }
    if (!request.changes || request.changes.length < 1 || request.changes.length > 16) {
        return controlFailure('INVALID_CONTROL_CHANGES');
    }
    var seen = {};
    var planned = [];
    for (var i = 0; i < request.changes.length; ++i) {
        var change = request.changes[i];
        var entry = controls.values[change.id];
        if (!entry || entry.group !== request.group || !entry.title || entry.active !== true || entry.value === null
                || change.id.indexOf('.automation_write') >= 0 || entry.title.toLowerCase().indexOf('[program') === 0
                || seen[change.id] || typeof change.value !== 'number' || !isFinite(change.value)
                || change.value < 0 || change.value > 1
                || ((entry.binary || entry.title === 'Bypass') && change.value !== 0 && change.value !== 1)) {
            return controlFailure('INVALID_CONTROL_CHANGES');
        }
        if (controls.plugins[entry.group]) {
            var plugin = controls.plugins[entry.group];
            var write = controls.values[entry.group + '.automation_write'];
            if (!plugin.name || !write || write.value !== 0) { return controlFailure('PLUGIN_NOT_READY'); }
        }
        if (entry.group === 'sends') {
            var sendNumber = Number(change.id.split('.')[1]) + 1;
            if (!(new RegExp('^' + sendNumber + '\\s*-\\s*\\S')).test(entry.object_title)) {
                return controlFailure('SEND_DESTINATION_REQUIRED');
            }
        }
        seen[change.id] = true;
        planned.push({title:entry.title, object_title:entry.object_title});
    }
    var result = {dispatched:true, code:'CONTROL_DISPATCHED', operation_id:request.operation_id};
    var identity = JSON.stringify(controls.plugins);
    for (var j = 0; j < request.changes.length; ++j) {
        var item = request.changes[j];
        var current = controls.values[item.id];
        if (!active || selected.uid !== request.uid || session !== request.session
                || JSON.stringify(controls.plugins) !== identity || current.title !== planned[j].title
                || current.object_title !== planned[j].object_title || current.active !== true) {
            return {dispatched:null, code:'CONTROL_TARGET_CHANGED_DURING_WRITE', completed:j};
        }
        current.surface.setProcessValue(activeDevice, item.value);
    }
    return result;
}
// MIDI input only parses and queues. Native binding writes run on the mapping's
// idle callback, after fresh cached identity/revision/transport guards.
input.mOnSysex = function (activeDevice, message) {
    if (message.length < 12 || message.length > 24000 || message[0] !== 240 || message[1] !== 125
            || message[2] !== 67 || message[3] !== 80 || message[4] !== 7 || message[message.length - 1] !== 247) { return; }
    var nonce = message[5];
    if (!nonce || message[6] !== 0 || message[7] !== 1 || (message.length - 9) % 3) { return; }
    var text = '';
    for (var i = 8; i < message.length - 1; i += 3) {
        if (message[i] > 127 || message[i + 1] > 127 || message[i + 2] > 3) { return; }
        text += String.fromCharCode(message[i] | message[i + 1] << 7 | message[i + 2] << 14);
    }
    var request;
    try { request = JSON.parse(text); } catch (error) { return; }
    if (!request || typeof request.operation_id !== 'string' || !/^[a-f0-9]{32}$/.test(request.operation_id)) { return; }
    var previous = controls.outcomes[request.operation_id];
    if (previous) {
        sendControls(activeDevice, nonce, previous.request === text ? previous.result : controlFailure('OPERATION_ID_CONFLICT'));
    } else if (controls.pending) {
        sendControls(activeDevice, nonce, controlFailure('CONTROL_BUSY'));
    } else {
        controls.pending = {nonce:nonce, request:request, text:text};
    }
};
page.mOnIdle = function (activeDevice, activeMapping) {
    var pending = controls.pending;
    if (!pending) { return; }
    controls.pending = null;
    // Record before dispatch. Exceptions or a lost reply must not cause a resend.
    var record = {request:pending.text, result:{dispatched:null, code:'CONTROL_OUTCOME_UNKNOWN'}};
    controls.outcomes[pending.request.operation_id] = record;
    controls.outcomeOrder.push(pending.request.operation_id);
    if (controls.outcomeOrder.length > 64) { delete controls.outcomes[controls.outcomeOrder.shift()]; }
    try { record.result = executeControls(activeDevice, pending.request); }
    catch (error) { record.result.detail = String(error); }
    sendControls(activeDevice, pending.nonce, record.result);
};
