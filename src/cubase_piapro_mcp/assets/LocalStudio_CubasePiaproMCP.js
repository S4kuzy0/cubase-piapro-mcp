// ES5 driver for Cubase LE 14. MIDI ports are deliberately separate from note input.
var api = require('midiremote_api_v1');
var driver = api.makeDeviceDriver('LocalStudio', 'CubasePiaproMCP', 'LocalStudio');
var input = driver.mPorts.makeMidiInput('CP MCP To Cubase');
var output = driver.mPorts.makeMidiOutput('CP MCP From Cubase');
driver.makeDetectionUnit().detectPortPair(input, output)
    .expectInputNameContains('CP MCP To Cubase')
    .expectOutputNameContains('CP MCP From Cubase');
var page = driver.mMapping.makePage('Data-first MCP');
var cache = {playing:127, recording:127, volume:127, pan:127, mute:127, solo:127};
function bind(name, cc, host, button) {
    var control = button ? driver.mSurface.makeButton(cc, 0, 1, 1) : driver.mSurface.makeKnob(cc, 1, 1, 1);
    control.mSurfaceValue.mMidiBinding.setInputPort(input).bindToControlChange(15, cc);
    var commands = {playing:'Start', recording:'Record', stopTrigger:'Stop'};
    if (commands[name]) {
        page.makeCommandBinding(control.mSurfaceValue, 'Transport', commands[name]).filterByValue(1);
        var feedback = driver.mSurface.makeCustomValueVariable(name + ' host feedback');
        page.makeValueBinding(feedback, host);
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
    var nonce = Math.round(value * 127);
    output.sendMidi(activeDevice, [240,125,67,80,2,nonce,cache.playing,cache.recording,
        cache.volume,cache.pan,cache.mute,cache.solo,247]);
};
