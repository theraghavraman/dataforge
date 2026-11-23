/* Business pilot hardening. This source is embedded in index.html by build-business.mjs.
 * Local controls are NOT authentication, authorization, a server backup or an audit service.
 */
const ProjectSafety = {
  limits: Object.freeze({ fileBytes: 256 * 2 ** 20, mappings: 500, workflows: 500, nodes: 5000, links: 20000, files: 10000 }),
  recordToken(record) { return !record ? null : record.revision || 'legacy:' + ProjectFile.hash(JSON.stringify(record.document)); },
  fail(message) { throw new Error(message); },
  array(value, name, max, optional = false) {
    if (optional && value === undefined) return [];
    if (!Array.isArray(value) || value.length > max) this.fail(`${name} must be an array of at most ${max} items.`);
    return value;
  },
  id(value, name) {
    if (typeof value !== 'string' || !value || value.length > 200 || ['__proto__', 'constructor', 'prototype'].includes(value)) this.fail(`Invalid ${name}.`);
    return value;
  },
  object(value, name) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) this.fail(`${name} must be an object.`);
    return value;
  },
  safeKeys(raw) {
    const todo = [[raw, 0]]; let seen = 0;
    while (todo.length) {
      const [item, depth] = todo.pop();
      if (!item || typeof item !== 'object') continue;
      if (++seen > 500000 || depth > 60) this.fail('Project structure is too large or too deeply nested.');
      for (const key of Object.keys(item)) {
        if (['__proto__', 'constructor', 'prototype'].includes(key)) this.fail('A reserved object key was found in the project. It was not opened.');
        if (item[key] && typeof item[key] === 'object') todo.push([item[key], depth + 1]);
      }
    }
  },
  base64Size(text, name) {
    if (typeof text !== 'string' || text.length % 4 || /[^A-Za-z0-9+/=]/.test(text) || /=[^=]/.test(text) || /={3}/.test(text)) this.fail(`${name} contains invalid base64 data.`);
    const size = text.length / 4 * 3 - (text.endsWith('==') ? 2 : text.endsWith('=') ? 1 : 0);
    if (size < 0) this.fail(`${name} contains invalid base64 data.`);
    return size;
  },
  encrypted(env) {
    if (env.formatVersion !== 1 || env.kdf?.name !== 'PBKDF2' || env.kdf?.hash !== 'SHA-256' || env.cipher?.name !== 'AES-GCM') this.fail('Unsupported encrypted-project envelope.');
    const count = env.kdf.iterations;
    if (!Number.isInteger(count) || count < 100000 || count > 2000000) this.fail('The encrypted project has an unsafe or excessive key-derivation work factor.');
    if (this.base64Size(env.kdf.salt, 'Salt') !== 16 || this.base64Size(env.cipher.iv, 'IV') !== 12 || this.base64Size(env.data, 'Ciphertext') < 16) this.fail('The encrypted project has invalid encryption parameters.');
  },
  graph(doc, workflow = false) {
    this.object(doc, 'Graph');
    if (!workflow && doc.version !== APP.formatVersion) this.fail('Unsupported mapping format version.');
    this.id(workflow ? doc.id : doc.mapping?.id, workflow ? 'workflow ID' : 'mapping ID');
    const nodes = this.array(doc.nodes, 'Graph nodes', this.limits.nodes);
    const links = this.array(doc.links, 'Graph links', this.limits.links);
    const ids = new Set();
    for (const node of nodes) {
      this.object(node, 'Node'); this.id(node.id, 'node ID');
      if (ids.has(node.id)) this.fail('Duplicate node ID in an imported graph.');
      ids.add(node.id);
      if (!(workflow ? TaskRegistry : Registry).get(node.type)) this.fail('An imported graph contains an unsupported node type.');
      if (node.config !== undefined) this.object(node.config, 'Node configuration');
    }
    const edgeIds = new Set();
    for (const link of links) {
      this.object(link, 'Link'); this.id(link.id, 'link ID');
      if (edgeIds.has(link.id)) this.fail('Duplicate link ID in an imported graph.');
      edgeIds.add(link.id);
      if (!ids.has(link.from) || !ids.has(link.to)) this.fail('An imported link refers to a missing node.');
    }
    return nodes;
  },
  inspect(raw) {
    this.object(raw, 'Project file'); this.safeKeys(raw);
    if (ProjectFile.isEncrypted(raw)) { this.encrypted(raw); return raw; }
    if (raw.version && Array.isArray(raw.nodes)) { this.graph(raw); return raw; }
    const bundle = ProjectFile.isBundle(raw) ? raw : null;
    if (bundle && bundle.formatVersion !== 1) this.fail('Unsupported project file format version.');
    const p = bundle ? bundle.project : raw;
    this.object(p, 'Project');
    if (p.projectVersion !== '1.0') this.fail('This is not a supported DataForge project or mapping file.');
    this.id(p.project?.id, 'project ID');
    const mappings = this.array(p.mappings, 'Mappings', this.limits.mappings);
    const workflows = this.array(p.workflows, 'Workflows', this.limits.workflows, true);
    const mids = new Map(), wids = new Set();
    for (const m of mappings) { this.graph(m); if (mids.has(m.mapping.id)) this.fail('Duplicate mapping ID in project.'); mids.set(m.mapping.id, m); }
    for (const w of workflows) { this.graph(w, true); if (wids.has(w.id)) this.fail('Duplicate workflow ID in project.'); wids.add(w.id); }
    this.array(p.parameterSets, 'Parameter sets', 5000, true);
    this.array(p.components, 'Components', 5000, true);
    if (p.versions !== undefined) {
      this.object(p.versions, 'Versions');
      for (const versions of Object.values(p.versions)) for (const version of this.array(versions, 'Mapping versions', 2000)) this.graph(version.doc);
    }
    if (bundle) {
      const files = this.array(bundle.files, 'Source files', this.limits.files, true), keys = new Set();
      let total = 0;
      for (const f of files) {
        this.object(f, 'Source file');
        const node = mids.get(f.mappingId)?.nodes.find(n => n.id === f.nodeId);
        if (!node || Registry.get(node.type)?.category !== 'source' || node.config?.fileName !== f.fileName || f.encoding !== 'base64') this.fail('A bundled source file does not match its mapping and source node.');
        const key = JSON.stringify([f.mappingId, f.nodeId, f.fileName]);
        if (keys.has(key)) this.fail('Duplicate source file in project bundle.'); keys.add(key);
        const size = this.base64Size(f.data, 'Source file');
        if (!Number.isSafeInteger(f.size) || f.size !== size) this.fail('A source file has an incorrect byte count.');
        total += size; if (total > this.limits.fileBytes) this.fail('Bundled source data exceeds the 256 MiB pilot limit. Save or import logic without source data.');
      }
      this.array(bundle.runs, 'Run history', 2000, true);
    }
    return raw;
  },
  async confirmImport() {
    return Dialog.confirm('Review imported content before running', 'Projects can contain executable SQL, workflow steps, parameters, test fixtures and source data. Import only files you trust. Importing does not itself run the project; review it before using Run. Imported source files may be stored unencrypted in this browser.', { ok: 'Trust and continue' });
  }
};

const Business = {
  ready: false, saveState: 'pending', lastSavedAt: null, lastError: null, tab: 'overview',
  stateId: 'business:state:v1', recoveryPrefix: 'business:recovery:',
  prefs: { autoRecovery: false }, events: [], points: [], estimate: null, persisted: null,
  stateTimer: null, recoveryTimer: null, stateQueue: Promise.resolve(), checkpointQueue: Promise.resolve(),
  maxPoints: 10, maxPointBytes: 4 * 2 ** 20, maxRecoveryBytes: 20 * 2 ** 20,
  eventLabels: Object.freeze({
    project_saved: 'Project committed to browser storage', save_failed: 'Browser save failed', save_conflict: 'Conflicting tab write blocked',
    checkpoint_created: 'Logic checkpoint created', checkpoint_restored: 'Logic checkpoint restored', checkpoint_deleted: 'Logic checkpoint deleted',
    recovery_enabled: 'Automatic recovery enabled', recovery_disabled: 'Automatic recovery disabled',
    project_exported: 'Project download requested', import_completed: 'Project import completed', import_rejected: 'Project import rejected',
    diagnostics_exported: 'Sanitized diagnostics exported', persistence_requested: 'Persistent storage requested', runtime_error: 'Unexpected runtime error observed'
  }),
  setSave(status, error = null) { this.saveState = status; this.lastError = error; this.renderStatus(); },
  conflict() {
    const first = !Repo.stale; Repo.stale = true; this.setSave('conflict');
    if (first) { this.log('save_conflict'); toast('A newer project exists in another tab. Browser writes are blocked here. Download a recovery copy, then reload this tab.', 'error', 12000); }
  },
  log(code) {
    if (!this.eventLabels[code]) return;
    const last = this.events[0];
    if (last?.code === code && Date.now() - Date.parse(last.at) < 30000) return;
    this.events.unshift({ id: dfUuid(), at: new Date().toISOString(), code });
    this.events = this.events.slice(0, 200);
    if (this.ready) { clearTimeout(this.stateTimer); this.stateTimer = setTimeout(() => this.persistState(), 180); }
  },
  async persistState() {
    clearTimeout(this.stateTimer); this.stateTimer = null;
    if (!Repo.db || Repo.stale) return false;
    const record = { id: this.stateId, prefs: { autoRecovery: this.prefs.autoRecovery === true }, events: this.events.slice(0, 200) };
    this.stateQueue = this.stateQueue.catch(() => false).then(async () => {
      try { await Repo.put('project', record); return true; }
      catch (_) { this.metadataError = true; return false; }
    });
    return this.stateQueue;
  },
  async stats() {
    try { this.estimate = await navigator.storage?.estimate?.() || null; } catch (_) { this.estimate = null; }
    try { this.persisted = await navigator.storage?.persisted?.() ?? null; } catch (_) { this.persisted = null; }
  },
  async refreshPoints() {
    this.points = Repo.db ? (await Repo.all('project')).filter(r => String(r.id).startsWith(this.recoveryPrefix) && r.kind === 'logic-checkpoint-v1').sort((a,b) => b.createdAt.localeCompare(a.createdAt)) : [];
    return this.points;
  },
  async init() {
    try {
      const saved = await Repo.get('project', this.stateId);
      this.prefs.autoRecovery = saved?.prefs?.autoRecovery === true;
      this.events = Array.isArray(saved?.events) ? saved.events.filter(e => this.eventLabels[e.code] && typeof e.at === 'string').slice(0, 200).map(e => ({ id: String(e.id), at: e.at, code: e.code })) : [];
      await this.refreshPoints();
    } catch (_) { this.metadataError = true; }
    this.ready = true;
    if (!$('#btnBusiness')) $('#btnAbout').before(h('button', { id: 'btnBusiness', class: 'tb icon', type: 'button', title: 'Business control center', 'aria-label': 'Business control center', html: svgIcon('settings'), onclick: () => this.open() }));
    if (Repo.channel) Repo.channel.onmessage = e => { if (e.data?.type === 'saved' && e.data.tab !== Repo.tabId && e.data.revision !== Repo._expectedRecord) this.conflict(); };
    if (Repo.db) Repo.db.addEventListener('close', () => { Repo.db = null; Repo.mode = 'memory'; this.setSave('memory'); toast('Browser storage closed. Export the project to keep your work.', 'error'); });
    await this.stats(); this.renderStatus();
    this.recoveryTimer = setInterval(() => this.autoCheckpoint(), 5 * 60 * 1000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') { Repo.flush(); this.persistState(); } });
    window.addEventListener('beforeunload', e => {
      const hasWork = Repo.project && (Repo.project.mappings.length + Repo.project.workflows.length + Repo.project.parameterSets.length > 0);
      const latestExport = ProjectUI.saved?.projectId === Repo.project.project.id && ProjectUI.saved?.hash === ProjectUI.fingerprint() && !Repo.dataChanged;
      const pending = Repo.db && !Repo.stale && Repo.signature() !== Repo.written;
      const inMemory = (!Repo.db || Repo.stale) && hasWork && !latestExport;
      if (state.running || !!Workflow.runState || pending || inMemory) { e.preventDefault(); e.returnValue = ''; }
    });
  },
  renderStatus() {
    if (!Repo.project) return;
    const status = Repo.stale ? 'conflict' : !Repo.db ? 'memory' : this.saveState;
    const texts = { saved: 'Saved in this browser', saving: 'Saving to browser...', pending: 'Browser save pending', error: 'Browser save failed - export now', conflict: 'Tab conflict - writes blocked', memory: 'Session only - export to keep work' };
    let el = $('#businessSaveStatus');
    if (!el && $('#projectNav')) { el = h('button', { id: 'businessSaveStatus', type: 'button', class: 'business-save', onclick: () => this.open(), title: 'Browser save state. This is not an external backup.' }); $('#projectState').after(el); }
    if (el) { el.dataset.state = status; el.textContent = texts[status] || texts.pending; }
    let banner = $('#businessConflict');
    if (!banner) {
      banner = h('div', { id: 'businessConflict', class: 'business-conflict', role: 'alert', hidden: true }, h('strong', { text: 'Another tab has newer work. Writes from this tab are blocked.' }),
        h('button', { class: 'btn', text: 'Download this tab', onclick: () => Repo.saveFile({ suffix: '-conflict-recovery' }) }),
        h('button', { class: 'btn', text: 'Reload latest', onclick: async () => { if (await Dialog.confirm('Reload the latest project?', 'This discards unexported changes in this tab. Download this tab first to keep a separate copy.', { ok: 'Reload', danger: true })) location.reload(); } }));
      document.body.append(banner);
    }
    banner.hidden = !Repo.stale;
  },
  async digest(text) {
    if (!globalThis.crypto?.subtle) throw new Error('Recovery checkpoints require HTTPS or localhost.');
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
  },
  checkpoint({ force = false } = {}) {
    // Serialize creates so automatic/manual checkpoints cannot bypass retention limits.
    const task = this.checkpointQueue.catch(() => null).then(() => this.createCheckpoint(force));
    this.checkpointQueue = task; return task;
  },
  async createCheckpoint(force) {
    if (!Repo.db || !(await Repo.guardFresh())) throw new Error('Durable browser storage in the current tab is required.');
    if (state.running || !!Workflow.runState) throw new Error('Finish the current run before creating a checkpoint.');
    if (Studio.mode === 'mapping') Repo.track();
    await Repo.flush();
    if (this.saveState !== 'saved') throw new Error('The current project must save successfully before creating a checkpoint.');
    const document = clone(Repo.project), json = JSON.stringify(document), bytes = new TextEncoder().encode(json).byteLength;
    if (bytes > this.maxPointBytes) throw new Error('Logic exceeds the 4 MiB checkpoint limit. Export a project file instead.');
    await this.refreshPoints();
    const fingerprint = ProjectFile.hash(JSON.stringify({ ...document, project: { ...document.project, updatedAt: '' } }));
    if (!force && this.points.some(p => p.projectId === document.project.id && p.fingerprint === fingerprint)) return null;
    const point = { id: this.recoveryPrefix + dfUuid(), kind: 'logic-checkpoint-v1', createdAt: new Date().toISOString(), projectId: document.project.id, projectName: document.project.name, bytes, fingerprint, json, digest: await this.digest(json) };
    await Repo.put('project', point);
    const ordered = [point, ...this.points]; let total = 0, count = 0;
    for (const item of ordered) { total += item.bytes; count++; if (count > this.maxPoints || total > this.maxRecoveryBytes) await Repo.del('project', item.id); }
    await this.refreshPoints(); this.log('checkpoint_created'); return point;
  },
  async manualCheckpoint() {
    const accepted = await Dialog.confirm('Create a local logic checkpoint?', 'This stores mapping logic, parameters and embedded test fixtures unencrypted in this browser. Uploaded source files and run history are excluded. A checkpoint is not an external backup and can disappear if browser data is cleared.', { ok: 'Create checkpoint' });
    if (!accepted) return;
    try { await this.checkpoint({ force: true }); toast('Logic checkpoint created. Source files are not included.', 'success'); await this.open('recovery'); }
    catch (e) { toast(e.message, 'error'); }
  },
  async autoCheckpoint() {
    if (!this.prefs.autoRecovery || !Repo.db || Repo.stale || state.running || !!Workflow.runState) return;
    if (!(Repo.project?.mappings.length || Repo.project?.workflows.length)) return;
    try { await this.checkpoint(); }
    catch (_) { this.metadataError = true; toast('Automatic recovery could not create a checkpoint. Export your project to keep an external copy.', 'warn', 8000); }
  },
  async restore(id) {
    try {
      if (!Repo.db || !(await Repo.guardFresh()) || state.running || !!Workflow.runState) throw new Error('Restore is unavailable while running, without storage, or in a stale tab.');
      const point = await Repo.get('project', id);
      if (!point || point.kind !== 'logic-checkpoint-v1' || typeof point.json !== 'string' || await this.digest(point.json) !== point.digest) throw new Error('This checkpoint is missing or damaged. Nothing was restored.');
      const raw = ProjectSafety.inspect(JSON.parse(point.json));
      if (!(await Dialog.confirm('Restore this logic checkpoint?', 'The current logic will be checkpointed first. Uploaded source files and run history are not part of a checkpoint. Reattach any sources that are missing after restore. This operation does not execute the mapping.', { ok: 'Restore logic', danger: true }))) return false;
      await this.checkpoint({ force: true });
      if (!(await Repo.guardFresh())) return false;
      this.disarmScheduler(); Repo.closeEditors(); Repo.activeId = null; Workflow.active = null; ProjectFile.password = null;
      Repo.project = Repo.normalize(raw); Repo.dataChanged = true;
      Studio.switch('mapping'); Actions.loadDoc(newDoc());
      if (Repo.project.mappings.length) await Repo.openMapping(Repo.project.mappings[0].mapping.id);
      else if (Repo.project.workflows.length) Workflow.open(Repo.project.workflows[0].id);
      else Welcome.open();
      if (Repo.project.mappings.length || Repo.project.workflows.length) $('#welcomeStage').hidden = true;
      Repo.save(); const saved = await Repo.flush();
      if (!saved) throw new Error('Restored logic is only in memory because the save failed. Export it before reloading.');
      Repo.render(); this.log('checkpoint_restored');
      toast('Logic restored. Review sources and export a new project file.', 'success'); await this.open('recovery'); return true;
    } catch (e) { toast(e.message, 'error'); return false; }
  },
  async deleteCheckpoint(id) {
    if (!(await Dialog.confirm('Delete this checkpoint?', 'Only this recovery point is deleted. The open project is not changed.', { ok: 'Delete checkpoint', danger: true }))) return;
    try { await Repo.del('project', id); await this.refreshPoints(); this.log('checkpoint_deleted'); await this.open('recovery'); }
    catch (e) { toast(e.message, 'error'); }
  },
  async requestPersistence() {
    try { this.persisted = await navigator.storage?.persist?.() ?? false; this.log('persistence_requested'); toast(this.persisted ? 'Persistent storage granted. External backups are still needed.' : 'Persistent storage was not granted. Keep external project backups.', this.persisted ? 'success' : 'warn'); }
    catch (_) { toast('This browser could not grant persistent storage.', 'warn'); }
    await this.open();
  },
  diagnostics() {
    // Explicit allow-list: do not add names, source rows, SQL, parameter values, paths, URLs or error messages.
    return { report: 'dataforge-sanitized-diagnostics', version: 1, at: new Date().toISOString(),
      app: { version: APP.version, duckdbPackage: APP.duckdb, deployment: 'single-user-browser-pilot', networkMode: document.querySelector('meta[name="dataforge-network"]')?.content || 'cdn' },
      runtime: { engine: Engine.status, secureContext: isSecureContext, indexedDB: Repo.mode, saveState: Repo.stale ? 'conflict' : this.saveState, lastBrowserCommit: this.lastSavedAt, persistentStorage: this.persisted },
      storage: this.estimate ? { usedBytes: this.estimate.usage, quotaBytes: this.estimate.quota } : null,
      counts: { mappings: Repo.project?.mappings.length || 0, workflows: Repo.project?.workflows.length || 0, parameterSets: Repo.project?.parameterSets.length || 0, checkpoints: this.points.length },
      controls: { atomicRevisionChecks: true, autoLogicRecovery: this.prefs.autoRecovery, exportPasswordSet: !!ProjectFile.password, browserStorageEncrypted: false, serverAuthentication: false, teamAuthorization: false, serverBackups: false, unattendedExecution: false },
      events: this.events.map(e => ({ at: e.at, code: e.code })),
      note: 'No project names, SQL, parameter values, source rows or file paths are included. Local event records are editable and are not compliance audit evidence.' };
  },
  exportDiagnostics() {
    downloadBlob(new Blob([JSON.stringify(this.diagnostics(), null, 2)], { type: 'application/json' }), 'dataforge-diagnostics.json');
    this.log('diagnostics_exported'); toast('Sanitized diagnostics downloaded. Review before sharing.', 'success');
  },
  statusCard(label, value, detail, tone = '') {
    return h('section', { class: 'business-card ' + tone }, h('span', { class: 'business-eyebrow', text: label }), h('strong', { text: value }), h('p', { text: detail }));
  },
  overview() {
    const storage = this.estimate ? `${fmtBytes(this.estimate.usage || 0)} used of approximately ${fmtBytes(this.estimate.quota || 0)}` : 'Storage estimate unavailable';
    const cards = h('div', { class: 'business-cards' },
      this.statusCard('Execution engine', Engine.ready ? 'Available' : 'Unavailable', Engine.ready ? 'Engine loaded. Validate and test each mapping before use.' : 'Design and export remain available; execution needs a working DuckDB dependency.', Engine.ready ? 'ok' : 'warn'),
      this.statusCard('Browser save', Repo.stale ? 'Conflict blocked' : !Repo.db ? 'Session only' : this.saveState === 'saved' ? 'Committed' : this.saveState, 'Browser storage is not a server backup.', Repo.stale || !Repo.db || this.saveState === 'error' ? 'warn' : 'ok'),
      this.statusCard('Logic recovery', String(this.points.length) + ' checkpoints', this.prefs.autoRecovery ? 'Automatic every 5 minutes when changed.' : 'Automatic checkpoints are off. Enable explicitly in Recovery.'),
      this.statusCard('Deployment scope', 'Single-user pilot', 'No shared repository, sign-in, access roles or server-side execution.', 'warn'));
    return h('div', null, cards,
      h('div', { class: 'business-note' }, h('strong', { text: 'Know the security boundary' }), h('p', { text: 'Password protection encrypts exported project files only. Browser projects, remembered source files and recovery checkpoints are not encrypted by this application. Preview masking is not access control.' })),
      h('section', { class: 'business-section' }, h('h3', { text: 'Storage and safeguards' }), h('p', { text: storage }), h('p', { class: 'small-muted', text: 'Persistent storage: ' + (this.persisted === null ? 'not reported' : this.persisted ? 'granted' : 'not granted') + '. Clearing site data can still remove all local work.' }),
        h('div', { class: 'btn-row' }, h('button', { class: 'btn', text: 'Request persistent storage', onclick: () => this.requestPersistence() }), h('button', { class: 'btn', text: 'Governance and masking', onclick: () => Governance.open() }), h('button', { class: 'btn', text: 'Protect exported project', onclick: () => ProjectUI.setPassword() }))),
      h('section', { class: 'business-section' }, h('h3', { text: 'External project backup' }), h('p', { text: 'Save the complete project to an approved location, then test reopening it. A browser download request is not proof that the file was retained or that restore will succeed.' }),
        h('div', { class: 'btn-row' }, h('button', { class: 'btn primary', text: 'Download complete project', onclick: () => Repo.saveFile() }), h('button', { class: 'btn', text: 'Download without source files', onclick: () => Repo.saveFile({ includeData: false }) }))));
  },
  recovery() {
    const toggle = h('input', { id: 'businessAutoRecovery', type: 'checkbox' }); toggle.checked = this.prefs.autoRecovery;
    toggle.onchange = async () => {
      const next = toggle.checked;
      if (next && !(await Dialog.confirm('Enable automatic logic recovery?', 'Every five minutes, changed project logic, parameter values and embedded test fixtures may be stored unencrypted on this device. Source file uploads and run history are excluded. Up to 10 checkpoints and 20 MiB are retained across projects.', { ok: 'Enable recovery' }))) { toggle.checked = false; return; }
      this.prefs.autoRecovery = next; this.log(next ? 'recovery_enabled' : 'recovery_disabled');
      const saved = await this.persistState();
      if (!saved) toast('The recovery setting is active only for this tab because it could not be saved.', 'warn');
    };
    return h('div', null,
      h('div', { class: 'business-note' }, h('strong', { text: 'Recovery points, not backups' }), h('p', { text: 'Checkpoints include project logic, parameters and embedded tests. They exclude uploaded source files and run history, but may still contain sensitive information. They are unencrypted, share the browser storage failure boundary, and are not protected by the export password.' })),
      h('section', { class: 'business-section' }, h('label', { class: 'chk', for: 'businessAutoRecovery' }, toggle, 'Create automatic logic checkpoints every 5 minutes'), h('p', { class: 'small-muted', text: 'Retention: up to 10 checkpoints, 20 MiB total, and 4 MiB per checkpoint. Limits apply across all local projects. Oldest points are removed first.' }),
        h('button', { class: 'btn primary', text: 'Create checkpoint now', disabled: !Repo.db || Repo.stale, onclick: () => this.manualCheckpoint() })),
      h('section', { class: 'business-section' }, h('h3', { text: 'Available checkpoints' }), this.points.length ? h('div', { class: 'business-points' }, ...this.points.map(p => h('div', { class: 'business-point' }, h('div', null, h('strong', { text: p.projectName }), h('small', { text: new Date(p.createdAt).toLocaleString() + ' / ' + fmtBytes(p.bytes) + ' / logic only' })),
        h('div', { class: 'btn-row' }, h('button', { class: 'btn', text: 'Restore', 'aria-label': 'Restore checkpoint ' + p.projectName, onclick: () => this.restore(p.id) }), h('button', { class: 'btn', text: 'Delete', 'aria-label': 'Delete checkpoint ' + p.projectName, onclick: () => this.deleteCheckpoint(p.id) }))))) : h('p', { class: 'business-empty', text: 'No checkpoints yet. Create one before a major change.' })));
  },
  activity() {
    return h('div', null, h('div', { class: 'business-note' }, h('strong', { text: 'Local operational history only' }), h('p', { text: 'The last 200 events in this browser are retained. They contain fixed event codes and timestamps, not source rows or SQL. These records are editable, have no verified user identity, and are not a compliance audit trail.' })),
      h('div', { class: 'business-activity' }, ...(this.events.length ? this.events.map(e => h('div', { class: 'business-event' }, h('time', { dateTime: e.at, text: new Date(e.at).toLocaleString() }), h('span', { text: this.eventLabels[e.code] }))) : [h('p', { class: 'business-empty', text: 'No events recorded yet.' })])));
  },
  deployment() {
    const rows = [
      ['Local design and recovery', 'IMPLEMENTED', 'Revision-checked saves, explicit storage state, checkpoints, bounded imports and sanitized diagnostics.'],
      ['Full data execution qualification', 'REQUIRED', 'Run the engine-backed smoke suite, generated-code checks and representative customer datasets. A loaded engine alone is not qualification.'],
      ['Identity and permissions', 'BACKEND REQUIRED', 'SSO, MFA, workspace isolation and server-enforced roles. A browser role dropdown would not enforce security.'],
      ['Shared persistence and backups', 'BACKEND REQUIRED', 'Managed project storage, encrypted object storage, tested restore and retention ownership.'],
      ['Unattended jobs and operations', 'BACKEND REQUIRED', 'Job workers, durable queues, retry policy, alerts, run limits and service monitoring. Browser timers stop when the tab closes.'],
      ['Release and security review', 'REQUIRED', 'Review dependencies, imported SQL, privacy requirements, deployment headers and the business acceptance checklist.']
    ];
    return h('div', null, h('div', { class: 'business-note' }, h('strong', { text: 'Pilot-ready controls are not an enterprise certification' }), h('p', { text: 'Use non-sensitive test data until execution, security, recovery and business acceptance are approved. The bundled server is a restricted static host, not a backend or identity provider.' })),
      ...rows.map(([label, status, text]) => h('section', { class: 'business-gate' }, h('div', null, h('h3', { text: label }), h('span', { class: 'business-tag', text: status })), h('p', { text }))),
      h('p', { class: 'small-muted', text: 'See BUSINESS_READINESS.md and DEPLOYMENT.md in the project ZIP for the deployment commands and release gates.' }));
  },
  async open(tab = 'overview') {
    this.tab = ['overview', 'recovery', 'activity', 'deployment'].includes(tab) ? tab : 'overview';
    try { await this.stats(); await this.refreshPoints(); } catch (_) { this.metadataError = true; }
    const body = $('#generalModalBody');
    const tabs = h('div', { class: 'business-tabs', role: 'tablist', 'aria-label': 'Business control center sections' });
    for (const [key, label] of [['overview', 'Overview'], ['recovery', 'Recovery'], ['activity', 'Activity'], ['deployment', 'Go-live gates']]) {
      const active = key === this.tab;
      const btn = h('button', { id: 'businessTab-' + key, type: 'button', role: 'tab', 'aria-selected': String(active), 'aria-controls': 'businessPanel', tabindex: active ? 0 : -1, text: label, onclick: () => { this.open(key).then(() => $('#businessTab-' + key)?.focus()); } });
      btn.onkeydown = e => { const buttons = [...tabs.children], i = buttons.indexOf(btn); if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const j = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length; buttons[j].click(); } };
      tabs.append(btn);
    }
    body.replaceChildren(h('div', { class: 'business-shell' },
      h('div', { class: 'business-header' }, h('div', null, h('span', { class: 'business-eyebrow', text: 'DATAFORGE / LOCAL BUSINESS PILOT' }), h('h2', { text: 'Business control center' }), h('p', { text: 'Protect your work. Understand your deployment limits.' })), h('span', { class: 'business-tag', text: 'SINGLE-USER' })),
      tabs, this.metadataError ? h('p', { class: 'business-note', text: 'Some recovery metadata could not be saved or read. Keep an external project export.' }) : null,
      h('div', { id: 'businessPanel', role: 'tabpanel', 'aria-labelledby': 'businessTab-' + this.tab }, this[this.tab]()),
      h('div', { class: 'business-footer' }, h('span', { text: 'Local records only. No telemetry is sent by these controls.' }), h('button', { class: 'btn', text: 'Download diagnostics', onclick: () => this.exportDiagnostics() }))));
    $('#generalModal').hidden = false;
    $('#businessTab-' + this.tab)?.focus();
  }
};

/* Atomic optimistic concurrency for the one shared browser project record.
 * The comparison and write occur in the same IndexedDB readwrite transaction.
 * BroadcastChannel is only a UI notification; correctness does not depend on it.
 */
Repo._expectedRecord = null;
Repo._writeQueue = Promise.resolve();
Repo._initializingBusiness = false;
Repo.get = function(store, id) {
  if (!this.db) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    let value, t;
    try { t = this.db.transaction(store, 'readonly'); const r = t.objectStore(store).get(id); r.onsuccess = () => { value = r.result; }; }
    catch (e) { reject(e); return; }
    t.oncomplete = () => { if (this._initializingBusiness && store === 'project' && id === 'current') this._expectedRecord = ProjectSafety.recordToken(value); resolve(value); };
    t.onabort = () => reject(t.error || new DOMException('Storage read aborted.', 'AbortError'));
    t.onerror = () => {};
  });
};
Repo.all = function(store) {
  if (!this.db) return Promise.resolve([]);
  return new Promise((resolve, reject) => {
    let value = [], t;
    try { t = this.db.transaction(store, 'readonly'); const r = t.objectStore(store).getAll(); r.onsuccess = () => { value = r.result; }; }
    catch (e) { reject(e); return; }
    t.oncomplete = () => resolve(value);
    t.onabort = () => reject(t.error || new DOMException('Storage read aborted.', 'AbortError'));
    t.onerror = () => {};
  });
};
Repo.guardFresh = async function() {
  if (this.stale) { Business.renderStatus(); return false; }
  if (!this.db) return true;
  try { const current = await this.get('project', 'current'); if (ProjectSafety.recordToken(current) !== this._expectedRecord) { Business.conflict(); return false; } return true; }
  catch (_) { Business.setSave('error', 'StorageReadError'); toast('Could not verify the stored project. Export your work before continuing.', 'error'); return false; }
};
Repo.changeStore = function(store, action, value) {
  if (!this.db) return Promise.resolve();
  if (this.stale) return Promise.reject(new Error('This tab is stale. Reload before writing browser data.'));
  return new Promise((resolve, reject) => {
    let t, failure;
    try {
      t = this.db.transaction([...new Set(['project', store])], 'readwrite');
      const r = t.objectStore('project').get('current');
      r.onsuccess = () => {
        try {
          if (ProjectSafety.recordToken(r.result) !== this._expectedRecord) { failure = new Error('A newer project exists. Browser writes are blocked.'); Business.conflict(); t.abort(); return; }
          t.objectStore(store)[action](value);
        } catch (e) { failure = e; t.abort(); }
      };
    } catch (e) { reject(e); return; }
    t.oncomplete = () => resolve();
    t.onabort = () => reject(failure || t.error || new DOMException('Storage write aborted.', 'AbortError'));
    t.onerror = () => {};
  });
};
Repo.put = function(store, value) { return this.changeStore(store, 'put', value); };
Repo.del = function(store, id) { return this.changeStore(store, 'delete', id); };
Repo.writeProject = function() {
  if (!this.project || !this.db) { Business.setSave('memory'); return Promise.resolve(false); }
  const document = clone(this.project), signature = JSON.stringify({ ...document, project: { ...document.project, updatedAt: '' } });
  this._writeQueue = this._writeQueue.catch(() => false).then(async () => {
    if (this.stale) { Business.setSave('conflict'); return false; }
    if (signature === this.written) { Business.setSave(this.signature() === this.written ? 'saved' : 'pending'); return true; }
    Business.setSave('saving');
    const revision = dfUuid();
    try {
      await new Promise((resolve, reject) => {
        const t = this.db.transaction('project', 'readwrite'), store = t.objectStore('project'), r = store.get('current'); let failure;
        r.onsuccess = () => {
          try {
            if (ProjectSafety.recordToken(r.result) !== this._expectedRecord) { failure = new Error('Project revision conflict.'); failure.name = 'ConflictError'; t.abort(); return; }
            store.put({ id: 'current', revision, document });
          } catch (e) { failure = e; t.abort(); }
        };
        t.oncomplete = () => { this._expectedRecord = revision; resolve(); };
        t.onabort = () => reject(failure || t.error || new DOMException('Project save aborted.', 'AbortError'));
        t.onerror = () => {};
      });
      this.written = signature; Business.lastSavedAt = new Date().toISOString(); Business.setSave(this.signature() === signature ? 'saved' : 'pending'); Business.log('project_saved');
      try { this.channel?.postMessage({ type: 'saved', tab: this.tabId, revision }); } catch (_) {}
      return true;
    } catch (e) {
      if (e.name === 'ConflictError') { Business.conflict(); return false; }
      Business.setSave('error', e.name || 'StorageError'); Business.log('save_failed');
      toast(e.name === 'QuotaExceededError' ? 'Browser storage is full. The project was not saved. Download a project file now.' : 'The project was not saved to browser storage. Download a project file before reloading.', 'error', 10000);
      return false;
    }
  });
  return this._writeQueue;
};
const originalRepoSave = Repo.save;
Repo.save = function() { if (this.project && !this.stale) Business.setSave(this.db ? 'pending' : 'memory'); return originalRepoSave.call(this); };
Repo.flush = function() {
  this.rememberActive(); if (!this.project) return Promise.resolve(false);
  clearTimeout(this.timer); this.timer = null; this.project.project.updatedAt = new Date().toISOString();
  return this.writeProject();
};
const originalRepoInit = Repo.init;
Repo.init = async function() { this._initializingBusiness = true; try { await originalRepoInit.call(this); } finally { this._initializingBusiness = false; } await Business.init(); };
const originalProjectRender = ProjectUI.render;
ProjectUI.render = function() { originalProjectRender.call(this); Business.renderStatus(); };
const originalCrashReport = Crash.report;
Crash.report = function(err) { Business.log('runtime_error'); return originalCrashReport.call(this, err); };
const originalSaveFile = Repo.saveFile;
Repo.saveFile = async function(options) { const name = await originalSaveFile.call(this, options); if (name) Business.log('project_exported'); return name; };
const originalNewProject = Repo.newProject;
Repo.newProject = async function() { if (!(await this.guardFresh())) return; Business.disarmScheduler(); return originalNewProject.call(this); };
const originalRemove = Repo.remove;
Repo.remove = async function(...args) { if (!(await this.guardFresh())) return; return originalRemove.apply(this, args); };
const originalProjectParse = ProjectFile.parse;
ProjectFile.parse = async function(bytes, name) {
  if (bytes.byteLength > ProjectSafety.limits.fileBytes) throw new Error('Project file exceeds the 256 MiB pilot import limit. Import logic without source data instead.');
  try { return ProjectSafety.inspect(await originalProjectParse.call(this, bytes, name)); }
  catch (e) { Business.log('import_rejected'); throw e; }
};
const originalProjectRead = ProjectFile.read;
ProjectFile.read = async function(file) { if (file.size > ProjectSafety.limits.fileBytes) throw new Error('Project file exceeds the 256 MiB pilot import limit.'); return originalProjectRead.call(this, file); };
const originalUnlock = ProjectFile.unlock;
ProjectFile.unlock = async function(env, password) { ProjectSafety.encrypted(env); return originalUnlock.call(this, env, password); };
const originalSources = Engine.sources;
Engine.sources = function() {
  if (document.querySelector('meta[name="dataforge-network"]')?.content !== 'local') return originalSources.call(this);
  const path = document.querySelector('meta[name="dataforge-duckdb"]')?.content || 'vendor/duckdb/';
  const base = new URL(path.replace(/\/*$/, '/'), location.href);
  if (base.origin !== location.origin) return [];
  return [{ label: 'self-hosted copy (no CDN fallback)', module: base.href + 'duckdb.mjs', dist: base.href }];
};

/* Browser scheduling is opt-in for this tab and never an unattended service. */
Business.schedulerArmed = false;
Business.disarmScheduler = function() {
  this.schedulerArmed = false; clearInterval(Workflow.scheduler); Workflow.scheduler = null; Workflow.scheduleAt = {};
  const hint = $('#scheduleCountdown'); if (hint) hint.textContent = 'Schedules paused. Arm reviewed workflows in the Business control center.';
};
const originalStartScheduler = Workflow.startScheduler;
Workflow.startScheduler = function() { if (!Business.schedulerArmed || Repo.stale) { Business.disarmScheduler(); return; } return originalStartScheduler.call(this); };
Business.toggleScheduler = async function() {
  if (this.schedulerArmed) this.disarmScheduler();
  else {
    if (!Engine.ready || !(await Repo.guardFresh())) { toast('A working engine and a current project are required to arm schedules.', 'warn'); return; }
    if (!(await Dialog.confirm('Arm browser schedules for this tab?', 'Review every scheduled workflow and its SQL first. Schedules can read loaded data and generate outputs while this tab stays open. They are not durable, can be delayed by browser throttling, and stop when the tab closes. Importing or restoring a project pauses them again.', { ok: 'Arm this tab' }))) return;
    this.schedulerArmed = true; Workflow.scheduleAt = {}; Workflow.startScheduler();
  }
  await this.open();
};
const businessOverview = Business.overview;
Business.overview = function() {
  const out = businessOverview.call(this);
  out.append(h('section', { class: 'business-section' }, h('h3', { text: 'Browser schedules' }), h('p', { text: this.schedulerArmed ? 'Armed for this tab only. Closing the tab stops scheduling.' : 'Paused by default. Imported and restored projects cannot automatically arm schedules.' }), h('button', { class: 'btn', text: this.schedulerArmed ? 'Pause browser schedules' : 'Arm reviewed schedules', onclick: () => this.toggleScheduler() })));
  return out;
};
const businessConflict = Business.conflict;
Business.conflict = function() { this.disarmScheduler(); return businessConflict.call(this); };
const originalWorkflowRender = Workflow.render;
Workflow.render = function(...args) { const out = originalWorkflowRender.apply(this, args); if (!Business.schedulerArmed && $('#scheduleCountdown')) $('#scheduleCountdown').textContent = 'Schedules paused. Arm reviewed workflows in the Business control center.'; return out; };
const originalActionsOpen = Actions.open;
Actions.open = async function(file) { if (Repo.project) return Repo.importProject(file); return originalActionsOpen.call(this, file); };
