const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const surfaces = new Map(), messages = [], bindings = [], hosts = [];
const callbacks = new WeakMap();
let idReads = 0, inHostCallback = false, inIdle = false, writes = 0, afterWrite = null;
function nativeCallbacks(object, names) {
    const stored = {};
    callbacks.set(object, stored);
    for (const name of names) Object.defineProperty(object, name, {
        get() { throw new Error('Native callback properties are write-only: ' + name); },
        set(fn) { stored[name] = fn; }, configurable: true
    });
    return object;
}
function value() { return {}; }
function surface(name) {
    const result = {mMidiBinding:{setInputPort() { return this; }, bindToControlChange() { return this; }},
        setProcessValue(d,v) {
            assert.ok(inIdle,'Binding writes must run on mapping idle, never a MIDI query/input callback');
            writes++;
            const h=bindings.find(pair=>pair[0]===result)[1];
            emit(h,'mOnProcessValueChange',v);
            if (afterWrite) afterWrite();
        }};
    if (name) surfaces.set(name, result);
    return result;
}
function plugin() {
    return nativeCallbacks({mOn:value(),mBypass:value(),mAutomationWrite:value(),
        mParameterBankZone:{setWrapAround() { return this; },makeParameterValue:value},
        accessSlotAtIndex() { return this; }}, ['mOnChangePluginIdentity']);
}
function host() {
    const h={mValue:Object.fromEntries(['Volume','Pan','Mute','Solo','Selected','InstrumentOpen','AutomationWrite']
        .map(x=>['m'+x,value()])),mInstrumentPluginSlot:plugin(),
        getUniqueIDString() { assert.ok(inHostCallback); idReads++; return this.assignedUID || 'native-id'; }};
    hosts.push(h); return h;
}
const selected=host();
selected.mChannelEQ=Object.fromEntries([1,2,3,4].map(i=>['mBand'+i,
    Object.fromEntries(['Gain','Freq','Q','On','FilterType'].map(x=>['m'+x,value()]))]));
selected.mSends={getByIndex() { return {mOn:value(),mPrePost:value(),mLevel:value()}; }};
selected.mInsertAndStripEffects={makeInsertEffectViewer:plugin};
function binding(s,h) {
    if (h) bindings.push([s,h]);
    return {filterByValue() { return this; },setTypeDefault() { return this; },
        setValueTakeOverModeJump() { return this; }};
}
const page={mHostAccess:{mTransport:{mValue:{mStart:value(),mStop:value(),mRecord:value()}},
    mTrackSelection:{mMixerChannel:selected},mMixConsole:{makeMixerBankZone() {
        return {includeAudioChannels(){return this;},includeInstrumentChannels(){return this;},includeSamplerChannels(){return this;},
            includeMIDIChannels(){return this;},setFollowVisibility(){return this;},makeMixerBankChannel:host,mAction:{mResetBank:{}}};
    }}},makeValueBinding:binding,makeCommandBinding:binding,makeActionBinding:binding};
const input={};
const driver={mPorts:{makeMidiInput(){return input;},makeMidiOutput(){return {sendMidi(d,m){messages.push(m);}};}},
    makeDetectionUnit(){return {detectPortPair(){return this;},expectInputNameContains(){return this;},expectOutputNameContains(){return this;}};},
    mSurface:{makeCustomValueVariable:surface,makeButton(){return {mSurfaceValue:surface()};},makeKnob(){return {mSurfaceValue:surface()};}},
    mMapping:{makePage(){return page;}}};
const api={makeDeviceDriver(){return driver;},mDefaults:{getNumberOfSendSlots(){return 4;}}};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{require(){return api;}});
function emit(object,name,...args) {
    inHostCallback=true;
    try {
        const fn=callbacks.has(object)?callbacks.get(object)[name]:object[name];
        if(fn) fn({}, {}, ...args);
    } finally {inHostCallback=false;}
}
// Real Cubase may deliver plugin identity before activating the mapping page.
emit(selected.mInstrumentPluginSlot,'mOnChangePluginIdentity','Test synth','Vendor','1.0','VST3');
emit(page,'mOnActivate');
emit(selected,'mOnTitleChange','Test channel');
for(const [s,h] of bindings) {
    if (!s || ![...surfaces.values()].includes(s)) continue;
    emit(h,'mOnProcessValueChange',.5);
    emit(h,'mOnTitleChange','Test channel','Parameter');
    emit(h,'mOnColorChange',0,0,0,0,true);
}
emit(selected.mValue.mAutomationWrite,'mOnProcessValueChange',0);
emit(page.mHostAccess.mTransport.mValue.mStart,'mOnProcessValueChange',0);
emit(page.mHostAccess.mTransport.mValue.mRecord,'mOnProcessValueChange',0);
const before=idReads;
surfaces.get('MCP controls group').mOnProcessValueChange({},1/127);
surfaces.get('MCP controls query').mOnProcessValueChange({},41/127);
assert.equal(idReads,before,'Extended queries must never dereference native host IDs');
assert.ok(messages.some(x=>x[4]===6),'New controls are discoverable after all bindings load');
function response(nonce) {
    const frames=messages.filter(x=>x[4]===6&&x[5]===nonce).sort((a,b)=>a[6]-b[6]);
    let text='';
    for(const frame of frames)for(let i=8;i<frame.length-1;i+=3)
        text+=String.fromCharCode(frame[i]|frame[i+1]<<7|frame[i+2]<<14);
    return JSON.parse(text);
}
function read() {
    messages.length=0;
    surfaces.get('MCP controls query').mOnProcessValueChange({},41/127);
    return response(41);
}
function send(request,nonce=42) {
    const text=JSON.stringify(request), payload=[240,125,67,80,7,nonce,0,1];
    for(let i=0;i<text.length;i++){const c=text.charCodeAt(i);payload.push(c&127,c>>7&127,c>>14&3);}
    payload.push(247);messages.length=0;
    input.mOnSysex({},payload);
}
function idle(){inIdle=true;try{emit(page,'mOnIdle');}finally{inIdle=false;}}
const snap=read();
assert.equal(snap.plugins.instrument.name,'Test synth','Activation must retain host identity delivered immediately before it');
const request={operation_id:'a'.repeat(32),session:snap.session,uid:snap.selected.uid,
    group:'eq',revision:snap.revision,changes:[{id:'eq.2.gain',value:.75}]};
send(request);
assert.equal(writes,0,'MIDI input only queues the request');
idle();assert.equal(writes,1);assert.equal(response(42).dispatched,true);
send(request);assert.equal(response(42).dispatched,true);idle();assert.equal(writes,1,'Same operation is not applied twice');
send({...request,changes:[{id:'eq.2.gain',value:.8}]});
assert.equal(response(42).code,'OPERATION_ID_CONFLICT');
const stale={...request,operation_id:'b'.repeat(32)};
send(stale);idle();assert.equal(response(42).code,'CONTROL_STATE_CHANGED');assert.equal(writes,1);
const current=read();
send({...request,operation_id:'c'.repeat(32),revision:current.revision,uid:'another-channel'});
idle();assert.equal(response(42).code,'CONTROL_TARGET_CHANGED');assert.equal(writes,1);
emit(selected.mValue.mAutomationWrite,'mOnProcessValueChange',1);
const automation=read();
send({...request,operation_id:'d'.repeat(32),revision:automation.revision});
idle();assert.equal(response(42).code,'AUTOMATION_WRITE_ENABLED');assert.equal(writes,1);
assert.equal(idReads,before,'All extended requests and idle writes use cached host identity');
emit(selected.mValue.mAutomationWrite,'mOnProcessValueChange',0);
const reentrant=read();
afterWrite=()=>{selected.assignedUID='changed-in-native-callback';emit(selected,'mOnTitleChange','Another channel');};
send({...request,operation_id:'e'.repeat(32),revision:reentrant.revision,
    changes:[{id:'eq.2.gain',value:.8},{id:'eq.3.gain',value:.8}]});
const start=writes;idle();
assert.equal(writes,start+1,'A native callback changing selection must stop the rest of a batch');
assert.equal(response(42).code,'CONTROL_TARGET_CHANGED_DURING_WRITE');
console.log('Control binding and write-only native callback contract passed');
