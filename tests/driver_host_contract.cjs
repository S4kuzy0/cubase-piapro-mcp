// Exercise the real driver with host handles that reject reads of empty slots.
// This is a native-boundary contract, not a mock of Cubase's audio engine.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const values = new Map(), zones = [], messages = [], invalidReads = [];
let idReads = 0, inHostCallback = false;
function value(name) {
    const result = {mMidiBinding: {
        setInputPort() { return this; }, bindToControlChange() { return this; }
    }};
    if (name) values.set(name, result);
    return result;
}
function host() {
    return {
        assigned: null,
        mValue: Object.fromEntries(['Volume','Pan','Mute','Solo','Selected','InstrumentOpen']
            .map(name => ['m' + name, value()])),
        mInstrumentPluginSlot: {mOn: value()},
        getUniqueIDString() {
            idReads++;
            if (!this.assigned || !inHostCallback) {
                invalidReads.push(this.assigned ? 'outside-host-callback' : 'empty-slot');
                throw new Error(invalidReads.at(-1));
            }
            return this.assigned.uid;
        }
    };
}
const selected = host();
function binding() {
    return {filterByValue() { return this; }, setTypeDefault() { return this; },
        setValueTakeOverModeJump() { return this; }};
}
const page = {
    mHostAccess: {
        mTransport: {mValue: {mStart:value(), mStop:value(), mRecord:value()}},
        mTrackSelection: {mMixerChannel:selected},
        mMixConsole: {makeMixerBankZone() {
            const zone = {kinds:[], hosts:[], mAction:{mResetBank:{}},
                includeAudioChannels() { this.kinds.push('audio'); return this; },
                includeInstrumentChannels() { this.kinds.push('instrument'); return this; },
                includeSamplerChannels() { this.kinds.push('sampler'); return this; },
                includeMIDIChannels() { this.kinds.push('midi'); return this; },
                setFollowVisibility() { return this; },
                makeMixerBankChannel() { const h=host(); this.hosts.push(h); return h; }};
            zones.push(zone); return zone;
        }}
    }, makeValueBinding:binding, makeCommandBinding:binding, makeActionBinding:binding
};
const driver = {
    mPorts: {makeMidiInput() { return {}; }, makeMidiOutput() {
        return {sendMidi(device, bytes) { messages.push(bytes); }};
    }},
    makeDetectionUnit() { return {detectPortPair() { return this; },
        expectInputNameContains() { return this; }, expectOutputNameContains() { return this; }}; },
    mSurface: {makeButton() { return {mSurfaceValue:value()}; },
        makeKnob() { return {mSurfaceValue:value()}; }, makeCustomValueVariable:value},
    mMapping: {makePage() { return page; }}
};
const context = {require() { return {makeDeviceDriver() { return driver; }}; }};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const mapping = {};
function emit(fn, ...args) {
    inHostCallback = true;
    try { if (fn) fn({}, mapping, ...args); } finally { inHostCallback = false; }
}
function assign(h, track) {
    h.assigned = track || null;
    emit(h.mOnTitleChange, track?.name || '');
    emit(h.mOnColorChange, 0,0,0,0, !!track);
    emit(h.mInstrumentPluginSlot.mOnChangePluginIdentity, track?.plugin || '');
    emit(h.mValue.mSelected.mOnProcessValueChange, track?.selected ? 1 : 0);
}
function project(tracks) {
    emit(page.mOnActivate);
    for (const zone of zones) {
        const filtered = tracks.filter(t => zone.kinds.includes(t.kind));
        zone.hosts.forEach((h,i) => assign(h, filtered[i]));
    }
    assign(selected, tracks.find(t => t.selected));
}
function snapshot() {
    messages.length = 0;
    const before = idReads;
    values.get('MCP inventory query').mOnProcessValueChange({}, 41/127);
    assert.equal(idReads, before, 'MIDI query must serialize cached metadata without native host reads');
    assert.equal(invalidReads.length, 0, 'Never dereference an unassigned native channel');
    const frames = messages.filter(x => x[4]===3).sort((a,b) => a[6]-b[6]);
    assert.ok(frames.length, 'Expected inventory frames, including for empty projects');
    let text='';
    for (const frame of frames) {
        for (let i=8; i<frame.length-1; i+=3) {
            text += String.fromCharCode(frame[i] | frame[i+1]<<7 | frame[i+2]<<14);
        }
    }
    return JSON.parse(text);
}
project([]);
assert.deepEqual(snapshot().tracks, []);
const midi={uid:'midi-1',name:'Route 🐳',kind:'midi',plugin:'Piapro Studio VSTi',selected:true};
project([midi]);
let result=snapshot();
assert.equal(result.tracks[0].uid,'midi-1');
assert.equal(result.tracks[0].kind,'midi');
assert.equal(result.selected.uid,'midi-1');
const instrument={uid:'owner-1',name:'Piapro',kind:'instrument',plugin:'Piapro Studio VSTi',selected:false};
project([instrument,midi]);
result=snapshot();
assert.deepEqual(result.tracks.map(t=>t.kind), ['instrument','midi']);
// No selected channel, deleted tracks, a project with identical names but new IDs.
project([instrument]);
assert.equal(snapshot().selected.uid,'');
project([]);
assert.equal(snapshot().tracks.length,0);
emit(page.mOnDeactivate);
project([{...midi,uid:'midi-new-project'}]);
assert.equal(snapshot().tracks[0].uid,'midi-new-project');
for (let i=0;i<100;i++) snapshot();
// Real Cubase reused a channel UID after removing/readding an instrument.
// Empty/refill callbacks must invalidate an old revision even if names match.
const priorLife=snapshot();
const recreated={...midi,uid:'midi-new-project'};
for(const zone of zones) zone.hosts.forEach(h=>assign(h,null));
assign(selected,null);
for(const zone of zones) {
    const filtered=[recreated].filter(t=>zone.kinds.includes(t.kind));
    zone.hosts.forEach((h,i)=>assign(h,filtered[i]));
}
assign(selected,recreated);
const nextLife=snapshot();
assert.deepEqual(nextLife.tracks,priorLife.tracks);
assert.ok(nextLife.generation>priorLife.generation,'An identical-looking recreated channel is a new cached lifetime');
console.log('Host-boundary lifecycle contract passed');
