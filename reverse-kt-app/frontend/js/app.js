/**
 * KT Assessment System — Frontend Application
 * Single-page app managing upload, knowledge review, assessment, and results flows.
 */

/** Base URL including `/api/v1` (absolute when possible). */
function resolveApiBase() {
    const metaRaw = document.querySelector('meta[name="kt-api-base"]')?.getAttribute('content')?.trim();
    if (metaRaw) {
        return metaRaw.replace(/\/+$/, '');
    }
    if (window.location.protocol === 'file:') {
        const ls = window.localStorage.getItem('KT_API_BASE')?.trim();
        if (ls) {
            return ls.replace(/\/+$/, '');
        }
        return 'http://127.0.0.1:8000/api/v1';
    }
    try {
        return new URL('/api/v1', window.location.href).href.replace(/\/+$/, '');
    } catch {
        return 'http://127.0.0.1:8000/api/v1';
    }
}

const API_BASE = resolveApiBase();
const TOKEN_KEY = 'KT_AUTH_TOKEN';
const ADMIN_LAST_SESSION_KEY = 'KT_LAST_ADMIN_SESSION';

/** Mirrors server env defaults until `/assessment/runtime-settings` loads */
const assessmentRuntime = {
    questionCount: 25,
    timeLimitMinutes: 60,
};

let assessmentCountdownInterval = null;

/** Logged-in user from `/auth/me` (null before login). */
let authUser = null;
let authToken = null;

/** `path` starts with `/`, e.g. `/upload`. */
async function apiFetch(path, opts = {}) {
    const headers = new Headers(opts.headers || {});
    const token = authToken?.trim() || localStorage.getItem(TOKEN_KEY)?.trim();
    if (token) {
        headers.set('Authorization', `Bearer ${token}`);
    }
    if (opts.body && !(opts.body instanceof FormData) && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
    }
    const res = await fetch(`${API_BASE}${path.startsWith('/') ? path : `/${path}`}`, {
        ...opts,
        headers,
    });
    if (res.status === 403 && token && path.includes('/assessment/')) {
        try {
            const ct = res.headers.get('content-type') || '';
            if (ct.includes('application/json')) {
                const j = await res.clone().json();
                const detail = typeof j.detail === 'string' ? j.detail : '';
                if (/time limit expired/i.test(detail)) {
                    showToast(detail, 'error');
                    logout();
                    return res;
                }
            }
        } catch {
            /* ignore */
        }
    }
    if (res.status === 401 && token && path !== '/auth/login') {
        authToken = null;
        authUser = null;
        localStorage.removeItem(TOKEN_KEY);
        window.location.reload();
    }
    return res;
}

function hideLoginOverlay() {
    $('#login-overlay')?.classList.add('hidden');
    const err = $('#login-error');
    if (err) err.textContent = '';
}

function clearAssessmentCountdown() {
    if (assessmentCountdownInterval) {
        clearInterval(assessmentCountdownInterval);
        assessmentCountdownInterval = null;
    }
    $('#assessment-countdown-strip')?.classList.add('hidden');
    const dv = $('#assessment-countdown-value');
    if (dv) dv.textContent = '—';
}

/** Start trainee countdown when API returns assessment_deadline_utc. */
function startAssessmentCountdownFromQuestionsPayload(payload) {
    clearAssessmentCountdown();
    const raw =
        payload && typeof payload.assessment_deadline_utc === 'string'
            ? payload.assessment_deadline_utc
            : '';
    const strip = $('#assessment-countdown-strip');
    if (!strip || !raw.trim()) return;
    const deadlineMs = new Date(raw).getTime();
    if (Number.isNaN(deadlineMs)) return;
    strip.classList.remove('hidden');
    const tick = () => {
        const left = deadlineMs - Date.now();
        const el = $('#assessment-countdown-value');
        if (left <= 0) {
            clearAssessmentCountdown();
            showToast('Assessment time is up — you have been signed out.', 'error');
            logout();
            return;
        }
        const m = Math.floor(left / 60000);
        const s = Math.floor((left % 60000) / 1000);
        if (el) el.textContent = `${m}:${String(s).padStart(2, '0')}`;
    };
    tick();
    assessmentCountdownInterval = setInterval(tick, 1000);
}

async function refreshAssessmentRuntimeFromServer() {
    try {
        const res = await fetch(`${API_BASE}/assessment/runtime-settings`);
        if (!res.ok) return;
        const j = await res.json();
        if (typeof j.assessment_question_count === 'number') {
            assessmentRuntime.questionCount = j.assessment_question_count;
        }
        if (typeof j.assessment_time_limit_minutes === 'number') {
            assessmentRuntime.timeLimitMinutes = j.assessment_time_limit_minutes;
        }
    } catch {
        /* offline / CORS — keep defaults */
    }
    applyAssessmentRuntimeChrome();
}

function applyAssessmentRuntimeChrome() {
    const cap = $('#btn-generate-caption');
    if (cap) {
        cap.textContent = `Generate assessment (${assessmentRuntime.questionCount} Q)`;
    }
}

function logout() {
    clearAssessmentCountdown();
    authToken = null;
    authUser = null;
    localStorage.removeItem(TOKEN_KEY);
    window.location.reload();
}

function wireLoginForm() {
    const form = $('#login-form');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const username = String(fd.get('username') || '').trim();
        const password = String(fd.get('password') || '');
        const errEl = $('#login-error');
        if (errEl) errEl.textContent = '';
        try {
            const res = await apiFetch('/auth/login', {
                method: 'POST',
                body: JSON.stringify({ username, password }),
            });
            if (!res.ok) {
                throw new Error(await parseErrorResponse(res));
            }
            const data = await res.json();
            authToken = data.access_token;
            localStorage.setItem(TOKEN_KEY, authToken);
            const me = await apiFetch('/auth/me', { method: 'GET' });
            if (!me.ok) {
                throw new Error('Could not load profile');
            }
            authUser = await me.json();
            hideLoginOverlay();
            await startAppShell();
        } catch (err) {
            const m = err instanceof Error ? err.message : String(err);
            if (errEl) errEl.textContent = m;
            showToast(m, 'error');
        }
    });
}

async function tryRestoreSession() {
    authToken = localStorage.getItem(TOKEN_KEY)?.trim() || null;
    if (!authToken) return false;
    const me = await apiFetch('/auth/me', { method: 'GET' });
    if (!me.ok) {
        localStorage.removeItem(TOKEN_KEY);
        authToken = null;
        authUser = null;
        return false;
    }
    authUser = await me.json();
    await startAppShell();
    return true;
}

function persistAdminSessionId(sessionId) {
    if (!sessionId || authUser?.role !== 'admin') return;
    try {
        localStorage.setItem(ADMIN_LAST_SESSION_KEY, sessionId);
    } catch {
        /* ignore */
    }
    refreshAssignLastSessionChip();
}

function refreshAssignLastSessionChip() {
    const row = $('#assign-last-session-chip');
    const code = $('#assign-last-session-display');
    if (!row || !code) return;
    let v = '';
    try {
        v = localStorage.getItem(ADMIN_LAST_SESSION_KEY) || '';
    } catch {
        /* ignore */
    }
    if (v.trim()) {
        code.textContent = v;
        row.classList.remove('hidden');
    } else {
        row.classList.add('hidden');
    }
}

function hideAdminSessionUi() {
    $('#session-id-banner')?.classList.add('hidden');
    $('#assessment-assignment-bar')?.classList.add('hidden');
}

function updateSessionIdDisplays(sessionId) {
    if (!sessionId || authUser?.role !== 'admin') return;
    const banner = $('#session-id-banner');
    const disp = $('#session-id-display');
    const abar = $('#assessment-assignment-bar');
    const aid = $('#assessment-assignment-id');
    if (disp) disp.textContent = sessionId;
    banner?.classList.remove('hidden');
    if (aid) aid.textContent = sessionId;
    abar?.classList.remove('hidden');
    persistAdminSessionId(sessionId);
}

async function copySessionIdFromElement(text) {
    const t = String(text || '').trim();
    if (!t || t === '—') {
        showToast('No Assignment ID available yet', 'info');
        return;
    }
    try {
        await navigator.clipboard.writeText(t);
        showToast('Assignment ID copied', 'success', 2500);
    } catch {
        showToast('Could not copy — select the text manually', 'error');
    }
}

function openAdminAssignWithSession(prefillSid) {
    const sid = String(prefillSid || state.sessionId || $('#session-id-display')?.textContent || '').trim();
    const input = $('#assign-session-id');
    if (input && sid) input.value = sid;
    switchView('admin');
    adminTabActivate('assign');
    refreshAssignLastSessionChip();
}

function initSessionIdControls() {
    $('#btn-copy-session-id')?.addEventListener('click', () =>
        copySessionIdFromElement($('#session-id-display')?.textContent || ''),
    );
    $('#btn-copy-assessment-assignment')?.addEventListener('click', () =>
        copySessionIdFromElement($('#assessment-assignment-id')?.textContent || ''),
    );
    $('#btn-goto-assign-from-session')?.addEventListener('click', () =>
        openAdminAssignWithSession($('#session-id-display')?.textContent || state.sessionId),
    );
    $('#btn-assign-fill-last-session')?.addEventListener('click', () => {
        let v = '';
        try {
            v = localStorage.getItem(ADMIN_LAST_SESSION_KEY) || '';
        } catch {
            /* ignore */
        }
        const input = $('#assign-session-id');
        if (input) input.value = v.trim();
        if (!v.trim()) showToast('No stored session yet — upload materials first', 'info');
    });
}

function applyRoleChrome() {
    const isAdmin = authUser?.role === 'admin';
    $('#btn-regen-knowledge')?.classList.toggle('hidden', !isAdmin);
    $('#btn-generate')?.classList.toggle('hidden', !isAdmin);
    const badge = $('#sidebar-role-badge');
    if (badge) badge.textContent = authUser?.role === 'admin' ? 'Admin' : 'Trainee';
    if (!isAdmin) hideAdminSessionUi();
    refreshSidebarAvailability();
}

function adminTabActivate(tabKey) {
    const tabs = {
        directory: { tabId: '#tab-admin-directory', paneId: '#admin-pane-directory' },
        'create-user': { tabId: '#tab-admin-create-user', paneId: '#admin-pane-create-user' },
        assign: { tabId: '#tab-admin-assign', paneId: '#admin-pane-assign' },
        results: { tabId: '#tab-admin-results', paneId: '#admin-pane-results' },
    };
    Object.entries(tabs).forEach(([k, ids]) => {
        $(ids.tabId)?.classList.toggle('active', k === tabKey);
        $(ids.paneId)?.classList.toggle('hidden', k !== tabKey);
    });
}

function initAdminTabs() {
    document.querySelectorAll('[data-admin-tab]').forEach((btn) => {
        btn.addEventListener('click', () => adminTabActivate(btn.dataset.adminTab));
    });
}

function syncAssessmentViewLayout() {
    const subtitle = $('#view-assessment-sub');
    const trainee = authUser?.role === 'user';
    const pick = $('#panel-trainee-pick-assignment');
    const flow = $('#panel-assessment-flow');
    const prog = $('#assessment-progress');
    const pendingCta = $('#assessment-pending-cta');

    if (subtitle) subtitle.textContent = trainee ? 'Select an assignment or continue where you left off.' : 'Answer generated questions once the admin has prepared the assessment.';

    if (trainee) {
        $('#assessment-assignment-bar')?.classList.add('hidden');
        if (!state.sessionId) {
            clearAssessmentCountdown();
        }
        pick?.classList.toggle('hidden', !!state.sessionId);
        flow?.classList.toggle('hidden', !state.sessionId);

        const hasQs = !!(state.questions && state.questions.length > 0);
        if (pendingCta) {
            if (state.sessionId && !hasQs) {
                pendingCta.classList.remove('hidden');
                pendingCta.innerHTML =
                    '<p class="muted">Questions are not available yet — wait until processing completes, then contact your administrator if this persists.</p>';
            } else {
                pendingCta.classList.add('hidden');
                pendingCta.innerHTML = '';
            }
        }

        prog?.classList.toggle('hidden', !hasQs);

        const qc = $('#question-container');
        if (state.sessionId && hasQs) {
            qc?.classList.remove('hidden');
            renderQuestion();
        } else if (state.sessionId && qc) {
            qc.classList.add('hidden');
            qc.innerHTML = '';
        } else if (qc) {
            qc.innerHTML = '';
        }

        const adminEmpty = $('#assessment-empty-hint');
        adminEmpty?.remove();
        return;
    }

    pick?.classList.add('hidden');
    flow?.classList.remove('hidden');
    prog?.classList.toggle('hidden', !(state.questions && state.questions.length > 0));
    pendingCta?.classList.add('hidden');
    const qc = $('#question-container');
    if (!(state.sessionId && state.questions?.length > 0)) {
        qc?.classList.remove('hidden');
        if (qc && !state.sessionId) {
            qc.innerHTML =
                '<p class="muted" id="assessment-empty-hint">Upload and process documents, then generate questions from the Knowledge page.</p>';
        } else if (qc && state.sessionId && !state.questions?.length) {
            qc.innerHTML =
                '<p class="muted" id="assessment-empty-hint">Generate the assessment from the Knowledge page.</p>';
        }
    }
}

async function populateAssignmentCards(container) {
    if (!container) return;
    container.innerHTML = '<p class="muted">Loading…</p>';
    const res = await apiFetch('/assignments/me');
    if (!res.ok) {
        container.innerHTML = `<p class="error-inline">${await parseErrorResponse(res)}</p>`;
        return;
    }
    const rows = await res.json();
    if (rows.length === 0) {
        container.innerHTML = '<p class="muted">No assessments assigned yet.</p>';
        return;
    }
    container.innerHTML = rows
        .map(
            (a) =>
                `<div class="assignment-card glass" data-aid="${a.id}" data-sid="${escapeHtml(a.session_id)}">` +
                `<div class="assignment-card-title">${escapeHtml(a.title || 'Assessment')}</div>` +
                `<div class="mono assignment-card-sid">${escapeHtml(a.session_id)}</div>` +
                `<div class="assignment-card-meta">${a.has_result ? 'Submitted' : 'Not submitted'}</div>` +
                `<button type="button" class="btn btn-primary btn-open-assignment">Open</button>` +
                (a.has_result ? `<button type="button" class="btn btn-secondary btn-view-result">Result</button>` : '') +
                `</div>`,
        )
        .join('');

    container.querySelectorAll('.btn-open-assignment').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
            const card = ev.target.closest('.assignment-card');
            const id = Number(card?.dataset.aid);
            const sid = card?.dataset.sid;
            if (id && sid) openTraineeAssignment(id, sid, false);
        });
    });
    container.querySelectorAll('.btn-view-result').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
            const card = ev.target.closest('.assignment-card');
            const id = Number(card?.dataset.aid);
            const sid = card?.dataset.sid;
            if (id && sid) openTraineeAssignment(id, sid, true);
        });
    });
}

function openEditUserModal(userRow) {
    const dlg = $('#modal-edit-user');
    if (!dlg || !userRow) return;
    $('#edit-user-id').value = String(userRow.id);
    $('#edit-user-name').value = userRow.username;
    $('#edit-user-fullname').value = userRow.full_name || '';
    $('#edit-user-password').value = '';
    $('#edit-user-active').checked = userRow.is_active !== false;
    if (dlg.showModal) dlg.showModal();
}

function wireUserEditModal() {
    $('#edit-user-cancel')?.addEventListener('click', () => $('#modal-edit-user')?.close());
    $('#edit-user-save')?.addEventListener('click', async () => {
        const id = $('#edit-user-id')?.value;
        const username = $('#edit-user-name')?.value?.trim();
        const full_name = $('#edit-user-fullname')?.value?.trim() || null;
        const password = $('#edit-user-password')?.value || '';
        const is_active = $('#edit-user-active')?.checked ?? true;

        const body = { username, full_name, is_active };
        if (password.length >= 6) body.password = password;

        const res = await apiFetch(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        if (!res.ok) {
            showToast(await parseErrorResponse(res), 'error');
            return;
        }
        $('#modal-edit-user')?.close();
        showToast('User updated', 'success');
        await refreshAdminLists();
    });
}

function initAdminForms() {
    $('#btn-create-user')?.addEventListener('click', adminCreateUser);
    $('#btn-create-assignment')?.addEventListener('click', adminCreateAssignment);
    $('#btn-open-create-user')?.addEventListener('click', () => adminTabActivate('create-user'));
}

async function refreshAdminLists() {
    try {
        const [uRes, rRes] = await Promise.all([apiFetch('/admin/users'), apiFetch('/admin/results')]);

        const sel = $('#assign-user-select');

        if (uRes.ok) {
            const users = await uRes.json();
            if (sel) {
                sel.innerHTML =
                    '<option value="">Trainee…</option>' +
                    users
                        .filter((u) => u.role === 'user' && u.is_active)
                        .map(
                            (u) =>
                                `<option value="${u.id}">${escapeHtml(u.username)}${u.full_name ? ` (${escapeHtml(u.full_name)})` : ''}</option>`,
                        )
                        .join('');
            }

            const tbody = $('#admin-users-table-body');
            if (tbody) {
                tbody.innerHTML = users
                    .map((u) => {
                        const isAdminRow = u.role === 'admin';
                        const actions = isAdminRow
                            ? `<span class="muted">—</span>`
                            : `<div class="cell-actions"><button type="button" class="btn-table" data-act="edit-user" data-id="${u.id}">Edit</button><button type="button" class="btn-table danger" data-act="del-user" data-id="${u.id}">Delete</button></div>`;
                        return (
                            `<tr data-user-id="${u.id}"><td>${u.id}</td>` +
                            `<td>${escapeHtml(u.username)}</td>` +
                            `<td>${escapeHtml(u.full_name || '')}</td>` +
                            `<td>${escapeHtml(u.role)}</td>` +
                            `<td>${u.is_active ? 'Yes' : 'No'}</td>` +
                            `<td>${actions}</td></tr>`
                        );
                    })
                    .join('');
                tbody.querySelectorAll('[data-act="edit-user"]').forEach((b) =>
                    b.addEventListener('click', async () => {
                        const id = Number(b.dataset.id);
                        const u = users.find((x) => x.id === id);
                        openEditUserModal(u);
                    }),
                );
                tbody.querySelectorAll('[data-act="del-user"]').forEach((b) =>
                    b.addEventListener('click', async () => {
                        const id = Number(b.dataset.id);
                        if (!confirm(`Delete user #${id}? This removes their assignments.`)) return;
                        const dr = await apiFetch(`/admin/users/${id}`, { method: 'DELETE' });
                        if (!dr.ok) {
                            showToast(await parseErrorResponse(dr), 'error');
                            return;
                        }
                        showToast('User deleted', 'success');
                        await refreshAdminLists();
                    }),
                );
            }
        }

        if (rRes.ok) {
            const rows = await rRes.json();
            const tb = $('#admin-results-body');
            if (tb) {
                adminEvaluationRowById.clear();
                tb.innerHTML = rows
                    .map((row) => {
                        adminEvaluationRowById.set(row.assignment_id, row);
                        const hasEval = Array.isArray(row.evaluation?.evaluations) && row.evaluation.evaluations.length > 0;
                        const evalCell = hasEval
                            ? `<button type="button" class="btn-link-eval" data-aid="${row.assignment_id}">View evaluation</button>`
                            : '<span class="muted">–</span>';
                        return (
                            `<tr>` +
                                `<td>${escapeHtml(row.trainee_username)}</td>` +
                                `<td class="mono">${escapeHtml(row.session_id)}</td>` +
                                `<td>${row.percentage != null ? `${Number(row.percentage).toFixed(1)}%` : '–'}</td>` +
                                `<td>${escapeHtml(row.readiness_level || '–')}</td>` +
                                `<td>${escapeHtml(String(row.submitted_at || '–'))}</td>` +
                                `<td>${evalCell}</td>` +
                                `</tr>`
                        );
                    })
                    .join('');
            }
        }
    } catch (e) {
        showToast(e instanceof Error ? e.message : String(e), 'error');
    }
    refreshAssignLastSessionChip();
}

async function adminCreateUser() {
    const username = $('#new-user-name')?.value?.trim();
    const password = $('#new-user-password')?.value ?? '';
    const full_name = $('#new-user-fullname')?.value?.trim() || null;
    if (!username || password.length < 6) {
        showToast('Username and password (6+ chars) required', 'error');
        return;
    }
    const res = await apiFetch('/admin/users', {
        method: 'POST',
        body: JSON.stringify({ username, password, full_name }),
    });
    if (!res.ok) {
        showToast(await parseErrorResponse(res), 'error');
        return;
    }
    $('#new-user-password').value = '';
    showToast('User created', 'success');
    await refreshAdminLists();
    adminTabActivate('directory');
}

async function adminCreateAssignment() {
    const sid = $('#assign-session-id')?.value?.trim();
    const uid = $('#assign-user-select')?.value;
    const title = $('#assign-title')?.value?.trim() || null;
    if (!sid || !uid) {
        showToast('Session id and trainee required', 'error');
        return;
    }
    const res = await apiFetch('/admin/assignments', {
        method: 'POST',
        body: JSON.stringify({
            session_id: sid,
            assigned_user_id: Number(uid),
            title,
        }),
    });
    if (!res.ok) {
        showToast(await parseErrorResponse(res), 'error');
        return;
    }
    showToast('Assessment assigned', 'success');
    await refreshAdminLists();
}

async function loadTraineeAssignments() {
    await populateAssignmentCards($('#trainee-assignments-inline'));
    await populateAssignmentCards($('#assignments-list'));
}

async function openTraineeAssignment(assignmentId, sessionId, resultsOnly) {
    clearAssessmentCountdown();
    state.sessionId = sessionId;
    state.assignmentId = assignmentId;
    state.knowledge = null;
    state.questions = [];
    state.answers = {};
    state.currentQuestion = 0;
    state.results = null;

    if (resultsOnly) {
        try {
            const det = await apiFetch(`/assignments/me/${assignmentId}`);
            if (!det.ok) throw new Error(await parseErrorResponse(det));
            const d = await det.json();
            state.results = d.evaluation;
            const qRes = await apiFetch(`/assessment/${sessionId}/questions`);
            if (qRes.ok) {
                const qd = await qRes.json();
                state.questions = qd.questions || [];
                clearAssessmentCountdown();
            }
            completeStep('knowledge');
            completeStep('assessment');
            enableStep('results');
            switchView('results');
            renderResults();
        } catch (e) {
            showToast(e instanceof Error ? e.message : String(e), 'error');
        }
        return;
    }

    const det = await apiFetch(`/assignments/me/${assignmentId}`);
    if (det.ok) {
        const d = await det.json();
        if (d.has_result && d.evaluation) {
            state.results = d.evaluation;
            const qRes = await apiFetch(`/assessment/${sessionId}/questions`);
            if (qRes.ok) {
                const qd = await qRes.json();
                state.questions = qd.questions || [];
                clearAssessmentCountdown();
            }
            completeStep('knowledge');
            completeStep('assessment');
            enableStep('results');
            switchView('results');
            renderResults();
            return;
        }
    }

    showProcessing('Preparing session…', 'Checking processing status');
    updateProgress(15);
    await pollProcessingStatus('assessment');

    const qRes = await apiFetch(`/assessment/${sessionId}/questions`, { method: 'GET' });
    if (qRes.ok) {
        const qd = await qRes.json();
        state.questions = qd.questions || [];
        state.currentQuestion = 0;
        state.answers = {};
        startAssessmentCountdownFromQuestionsPayload(qd);
    }
    switchView('assessment');
    syncAssessmentViewLayout();
}

async function startAppShell() {
    hideLoginOverlay();

    $('#app-shell')?.classList.remove('hidden');

    await refreshAssessmentRuntimeFromServer();

    const sul = $('#sidebar-user-line');
    if (sul) {
        const u = authUser?.username ?? '';
        const fn = authUser?.full_name?.trim();
        sul.textContent = fn ? `${u} — ${fn}` : u;
    }

    $('#btn-sidebar-logout')?.addEventListener('click', () => logout());

    initUpload();
    initNavigation();
    initAssessmentNav();
    initActions();
    initAdminForms();
    initAdminTabs();
    wireUserEditModal();
    wireAdminEvaluationModal();
    initSessionIdControls();

    if (authUser?.role === 'user') {
        state.stepUnlock.upload = true;
        state.stepUnlock.knowledge = true;
        state.stepUnlock.assessment = true;
        state.stepUnlock.results = false;
        ['upload', 'knowledge', 'assessment'].forEach((k) => navSidebar(k)?.classList.remove('completed'));
    } else {
        resetWizardSidebar();
    }

    applyRoleChrome();
    refreshAssignLastSessionChip();
    if (authUser?.role === 'admin' && state.sessionId) {
        updateSessionIdDisplays(state.sessionId);
    }

    if (authUser?.role === 'user') {
        switchView('assessment');
        populateAssignmentCards($('#trainee-assignments-inline')).catch(() => {});
        $('#panel-trainee-pick-assignment')?.classList.remove('hidden');
        $('#btn-new-session-label').textContent = 'Back to assignments';
        return;
    }

    $('#btn-new-session-label').textContent = 'New session';
    switchView('upload');
}

async function parseErrorResponse(res) {
    try {
        const ct = res.headers.get('content-type') || '';
        if (ct.includes('application/json')) {
            const j = await res.json();
            const d = j.detail;
            if (typeof d === 'string') return d;
            if (Array.isArray(d)) {
                return d
                    .map((item) => {
                        if (typeof item === 'string') return item;
                        if (item?.msg) return item.msg;
                        if (item?.message) return item.message;
                        return JSON.stringify(item);
                    })
                    .join(' ');
            }
            if (typeof j.message === 'string') return j.message;
            return JSON.stringify(j);
        }
        const text = await res.text();
        return text.trim().slice(0, 400) || `${res.status} ${res.statusText}`;
    } catch {
        return `${res.status} ${res.statusText}`;
    }
}

function setUploadError(message) {
    const el = $('#upload-error-banner');
    if (!el) return;
    if (!message) {
        el.textContent = '';
        el.classList.remove('visible');
        return;
    }
    el.textContent = message;
    el.classList.add('visible');
}

function friendlyNetworkError(err) {
    if (!err) return 'Request failed';
    const m = String(err.message || '');
    if (err.name === 'TypeError' && (m.includes('fetch') || m.includes('Load failed') || m.includes('NetworkError'))) {
        return (
            'Cannot reach the API. Serve the app from http://127.0.0.1:8000 with the backend running, ' +
            'or set localStorage.KT_API_BASE to http://127.0.0.1:8000/api/v1 when opening index.html as a file.'
        );
    }
    return m || String(err);
}
// ── State ──────────────────────────────────────────────────────────
const state = {
    sessionId: null,
    assignmentId: null,
    files: [],
    knowledge: null,
    questions: [],
    answers: {},
    currentQuestion: 0,
    results: null,
    processing: false,
    /** When set, only this sidebar destination stays enabled (during async pipelines). */
    sidebarExclusive: null,
    /** Admin wizard gates; trainee uses results only. */
    stepUnlock: {
        upload: true,
        knowledge: false,
        assessment: false,
        results: false,
    },
};

/** Admin results table rows keyed by assignment id for “View details”. */
const adminEvaluationRowById = new Map();

// ── DOM References ─────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const views = {
    upload: $('#view-upload'),
    knowledge: $('#view-knowledge'),
    assessment: $('#view-assessment'),
    results: $('#view-results'),
    myAssignments: $('#view-my-assignments'),
    admin: $('#view-admin'),
};

function navSidebar(key) {
    return $(`#sidebar-nav-${key}`);
}

function escapeHtml(text) {
    const s = String(text ?? '');
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function beginExclusiveSidebar(key) {
    state.sidebarExclusive = key;
    refreshSidebarAvailability();
}

function releaseExclusiveSidebar() {
    state.sidebarExclusive = null;
    refreshSidebarAvailability();
}

function refreshSidebarAvailability() {
    const isAdmin = authUser?.role === 'admin';
    const isUser = authUser?.role === 'user';
    const ex = state.sidebarExclusive;

    const keys = ['upload', 'knowledge', 'assessment', 'results', 'admin'];
    keys.forEach((key) => {
        const el = navSidebar(key);
        if (!el) return;

        const hideForTrainee = isUser && (key === 'upload' || key === 'knowledge' || key === 'admin');
        el.classList.toggle('hidden-nav-item', hideForTrainee);
        if (hideForTrainee) {
            el.classList.remove('active');
            return;
        }

        let wizardBlocked = false;
        if (isAdmin) {
            wizardBlocked =
                key === 'knowledge'
                    ? !state.stepUnlock.knowledge
                    : key === 'assessment'
                      ? !state.stepUnlock.assessment
                      : key === 'results'
                        ? !state.stepUnlock.results
                        : false;
        } else if (isUser) {
            wizardBlocked = key === 'results' && !state.stepUnlock.results;
        }

        const exclusiveBlocked = !!(ex && ex !== key);
        el.disabled = wizardBlocked || exclusiveBlocked;
        el.classList.toggle('nav-locked', !!(ex && key !== ex));
    });
}

function resetWizardSidebar() {
    state.stepUnlock.upload = true;
    state.stepUnlock.knowledge = false;
    state.stepUnlock.assessment = false;
    state.stepUnlock.results = false;
    ['upload', 'knowledge', 'assessment', 'results'].forEach((k) => navSidebar(k)?.classList.remove('completed'));
    refreshSidebarAvailability();
}

document.addEventListener('DOMContentLoaded', async () => {
    wireLoginForm();
    const ok = await tryRestoreSession();
    if (!ok) {
        $('#login-overlay')?.classList.remove('hidden');
    }
});

// ── Navigation ─────────────────────────────────────────────────────
function initNavigation() {
    document.querySelectorAll('#sidebar-nav .sidebar-link').forEach((btn) => {
        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            const nav = btn.dataset.nav;
            if (!nav) return;
            if (nav === 'admin') {
                adminTabActivate('directory');
            }
            switchView(nav);
        });
    });
}

function switchView(viewName) {
    const target = views[viewName];
    if (!target) return;

    Object.values(views).forEach((v) => {
        if (v) v.classList.remove('active');
    });
    target.classList.add('active');

    const keys = ['upload', 'knowledge', 'assessment', 'results', 'admin'];
    keys.forEach((k) => {
        const b = navSidebar(k);
        if (!b || b.classList.contains('hidden-nav-item')) return;
        const isActive = k === viewName || (viewName === 'my-assignments' && k === 'assessment');
        b.classList.toggle('active', isActive);
        if (isActive) b.setAttribute('aria-current', 'page');
        else b.removeAttribute('aria-current');
    });

    if (viewName === 'assessment' || viewName === 'my-assignments') {
        if (authUser?.role === 'user') {
            populateAssignmentCards($('#trainee-assignments-inline')).catch(() => {});
        }
        if (authUser?.role === 'admin' && state.sessionId) {
            updateSessionIdDisplays(state.sessionId);
        }
        syncAssessmentViewLayout();
    }

    if (viewName === 'admin') {
        refreshAdminLists().catch(() => {});
    }
}

function enableStep(stepName) {
    if (Object.prototype.hasOwnProperty.call(state.stepUnlock, stepName)) {
        state.stepUnlock[stepName] = true;
    }
    refreshSidebarAvailability();
}

function completeStep(stepName) {
    navSidebar(stepName)?.classList.add('completed');
}

// ── File Upload ────────────────────────────────────────────────────
function initUpload() {
    const dropzone = $('#upload-dropzone');
    const fileInput = $('#file-input');
    const uploadBtn = $('#btn-upload');

    // Click to browse
    dropzone.addEventListener('click', () => fileInput.click());

    // File input change
    fileInput.addEventListener('change', (e) => {
        addFiles(Array.from(e.target.files));
        fileInput.value = '';
    });

    // Drag & drop
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('drag-over');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('drag-over');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
        addFiles(Array.from(e.dataTransfer.files));
    });

    // Upload button (stopPropagation so stray handlers never swallow the click)
    uploadBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        uploadFiles();
    });
}

function addFiles(files) {
    const validExts = ['.pdf', '.vtt', '.txt', '.text', '.docx'];

    files.forEach(file => {
        const ext = '.' + file.name.split('.').pop().toLowerCase();
        if (!validExts.includes(ext)) {
            showToast(`Unsupported file type: ${ext}`, 'error');
            return;
        }
        // Avoid duplicates
        if (state.files.find(f => f.name === file.name)) return;
        state.files.push(file);
    });

    renderFileList();
    $('#btn-upload').disabled = state.files.length === 0;
}

function removeFile(index) {
    state.files.splice(index, 1);
    renderFileList();
    $('#btn-upload').disabled = state.files.length === 0;
}

function renderFileList() {
    const container = $('#file-list');
    container.innerHTML = '';

    state.files.forEach((file, index) => {
        const ext = file.name.split('.').pop().toLowerCase();
        const sizeKB = (file.size / 1024).toFixed(1);

        const item = document.createElement('div');
        item.className = 'file-item';
        item.innerHTML = `
            <div class="file-icon ${ext}">${ext}</div>
            <div class="file-info">
                <div class="file-name">${file.name}</div>
                <div class="file-size">${sizeKB} KB</div>
            </div>
            <button type="button" class="file-remove" data-index="${index}" title="Remove file">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        `;
        container.appendChild(item);
    });

    // Attach remove handlers
    container.querySelectorAll('.file-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeFile(parseInt(btn.dataset.index));
        });
    });
}

async function uploadFiles() {
    if (state.files.length === 0) return;

    const formData = new FormData();
    state.files.forEach(f => formData.append('files', f));

    const uploadBtn = $('#btn-upload');
    uploadBtn.disabled = true;
    setUploadError('');

    showProcessing('Uploading files...', 'Sending your documents for processing');
    beginExclusiveSidebar('upload');

    try {
        const res = await apiFetch('/upload', {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            const msg = await parseErrorResponse(res);
            throw new Error(msg || 'Upload failed');
        }

        const data = await res.json();
        state.sessionId = data.session_id;
        updateSessionIdDisplays(data.session_id);

        showToast('Files uploaded successfully!', 'success');
        updateProgress(20);
        updateProcessingText('Processing documents...', 'Extracting content and structure');

        await pollProcessingStatus();
    } catch (err) {
        const display = err instanceof Error ? friendlyNetworkError(err) : String(err);
        setUploadError(display);
        showToast(display, 'error');
        console.error('Upload failed:', err);
        hideProcessing();
        uploadBtn.disabled = false;
    } finally {
        releaseExclusiveSidebar();
    }
}

async function pollProcessingStatus(exclusiveNav = null) {
    const statusMap = {
        pending: { pct: 20, title: 'Queued...', msg: 'Waiting to start processing' },
        processing: { pct: 40, title: 'Parsing documents...', msg: 'Extracting text and structure' },
        extracting_knowledge: { pct: 65, title: 'Extracting knowledge...', msg: 'AI is analyzing your content' },
        building_index: { pct: 85, title: 'Building search index...', msg: 'Creating vector embeddings for RAG' },
    };

    if (exclusiveNav) beginExclusiveSidebar(exclusiveNav);

    try {
        while (true) {
            try {
                const res = await apiFetch(`/upload/${state.sessionId}/status`, { method: 'GET' });
                if (!res.ok) {
                    const msg = await parseErrorResponse(res);
                    throw new Error(msg || 'Status request failed');
                }
                const data = await res.json();

                if (data.status === 'completed') {
                    setUploadError('');
                    updateProgress(100);
                    updateProcessingText('Complete!', 'Processing finished successfully');
                    await sleep(800);
                    hideProcessing();
                    $('#btn-upload').disabled = false;

                    if (exclusiveNav === 'assessment') {
                        return;
                    }

                    await loadKnowledge();
                    completeStep('upload');
                    enableStep('knowledge');
                    switchView('knowledge');
                    return;
                }

                if (data.status === 'failed') {
                    throw new Error(data.message || 'Processing failed');
                }

                const info = statusMap[data.status] || { pct: 50, title: 'Processing...', msg: '' };
                updateProgress(info.pct);
                updateProcessingText(info.title, info.msg);
            } catch (err) {
                const display = err instanceof Error ? friendlyNetworkError(err) : String(err);
                setUploadError(display);
                showToast(display, 'error');
                console.error('Processing status failed:', err);
                hideProcessing();
                $('#btn-upload').disabled = false;
                return;
            }

            await sleep(2000);
        }
    } finally {
        if (exclusiveNav) releaseExclusiveSidebar();
    }
}

// ── Knowledge View ─────────────────────────────────────────────────
async function loadKnowledge() {
    try {
        const res = await apiFetch(`/upload/${state.sessionId}/knowledge`, { method: 'GET' });
        if (!res.ok) {
            const msg = await parseErrorResponse(res);
            throw new Error(msg || 'Failed to load knowledge');
        }
        state.knowledge = await res.json();
        renderKnowledge();
        if (authUser?.role === 'admin' && state.sessionId) {
            updateSessionIdDisplays(state.sessionId);
        }
    } catch (err) {
        const display = err instanceof Error ? friendlyNetworkError(err) : String(err);
        showToast(display, 'error');
    }
}

function renderKnowledge() {
    const k = state.knowledge;
    if (!k) return;

    // Stats
    const statsRow = $('#knowledge-stats');
    statsRow.innerHTML = `
        <div class="stat-card">
            <div class="stat-value">${k.units?.length || 0}</div>
            <div class="stat-label">Knowledge Units</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${k.total_concepts || 0}</div>
            <div class="stat-label">Concepts</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${k.total_from_pdf || 0}</div>
            <div class="stat-label">From PDF</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${k.total_from_transcript || 0}</div>
            <div class="stat-label">From Transcript</div>
        </div>
    `;

    // Summary text
    $('#knowledge-text').textContent = k.summary || 'Knowledge extracted successfully.';

    // Knowledge units
    const unitsContainer = $('#knowledge-units');
    unitsContainer.innerHTML = '';

    (k.units || []).forEach((unit, idx) => {
        const el = document.createElement('div');
        el.className = 'knowledge-unit';
        el.innerHTML = `
            <div class="unit-header">
                <span class="unit-topic">${unit.topic}</span>
                <span class="unit-source ${unit.source_type}">${unit.source_type}</span>
            </div>
            <div class="unit-tags">
                ${(unit.concepts || []).slice(0, 4).map(c => `<span class="unit-tag">${c}</span>`).join('')}
                ${(unit.concepts || []).length > 4 ? `<span class="unit-tag">+${unit.concepts.length - 4} more</span>` : ''}
            </div>
            <div class="unit-details">
                ${renderDetailSection('Concepts', unit.concepts)}
                ${renderDetailSection('Steps / Processes', unit.steps)}
                ${renderDetailSection('Tools & Technologies', unit.tools)}
                ${renderDetailSection('Decisions', unit.decisions)}
                ${renderDetailSection('Common Issues', unit.common_issues)}
                ${renderDetailSection('Key Insights', unit.key_insights)}
            </div>
        `;

        el.addEventListener('click', () => el.classList.toggle('expanded'));
        unitsContainer.appendChild(el);
    });
}

function renderDetailSection(title, items) {
    if (!items || items.length === 0) return '';
    return `
        <div class="detail-section">
            <h5>${title}</h5>
            <ul>${items.map(i => `<li>${i}</li>`).join('')}</ul>
        </div>
    `;
}

// ── Assessment ─────────────────────────────────────────────────────
function initActions() {
    $('#btn-regen-knowledge')?.addEventListener('click', async () => {
        if (!state.sessionId) return;
        const btn = $('#btn-regen-knowledge');
        btn.disabled = true;
        try {
            const res = await apiFetch(`/upload/${state.sessionId}/reprocess`, { method: 'POST' });
            if (!res.ok) {
                throw new Error(await parseErrorResponse(res));
            }
            showToast('Re-processing started…', 'info');
            showProcessing('Re-processing…', 'Rebuilding knowledge and index');
            await pollProcessingStatus('knowledge');
        } catch (e) {
            showToast(e.message || String(e), 'error');
            hideProcessing();
        } finally {
            btn.disabled = false;
        }
    });

    $('#btn-generate')?.addEventListener('click', generateAssessment);

    $('#btn-new-session')?.addEventListener('click', () => {
        if (authUser?.role === 'user') {
            clearAssessmentCountdown();
            state.sessionId = null;
            state.assignmentId = null;
            state.knowledge = null;
            state.questions = [];
            state.answers = {};
            state.currentQuestion = 0;
            state.results = null;
            populateAssignmentCards($('#trainee-assignments-inline')).catch(() => {});
            switchView('assessment');
            syncAssessmentViewLayout();
            return;
        }
        clearAssessmentCountdown();
        hideAdminSessionUi();
        state.sessionId = null;
        state.assignmentId = null;
        state.files = [];
        state.knowledge = null;
        state.questions = [];
        state.answers = {};
        state.currentQuestion = 0;
        state.results = null;
        renderFileList();
        $('#btn-upload').disabled = true;
        setUploadError('');

        resetWizardSidebar();
        switchView('upload');
    });
}

async function generateAssessment() {
    const btn = $('#btn-generate');
    if (btn) btn.disabled = true;

    showProcessing('Generating assessment…', 'Drafting questions — this may take a minute');
    updateProgress(12);
    beginExclusiveSidebar('knowledge');

    let progressTimer;
    try {
        let p = 12;
        progressTimer = setInterval(() => {
            p = Math.min(p + 4, 72);
            updateProgress(p);
        }, 900);

        const res = await apiFetch(`/assessment/${state.sessionId}/generate`, {
            method: 'POST',
            body: JSON.stringify({ difficulty_mix: 'balanced' }),
        });

        if (!res.ok) {
            throw new Error(await parseErrorResponse(res));
        }

        updateProgress(82);
        const qRes = await apiFetch(`/assessment/${state.sessionId}/questions`, { method: 'GET' });
        if (!qRes.ok) {
            throw new Error(await parseErrorResponse(qRes));
        }

        const data = await qRes.json();
        state.questions = data.questions || [];
        state.currentQuestion = 0;
        state.answers = {};

        updateProgress(100);
        updateProcessingText('Complete!', 'Assessment is ready');
        await sleep(500);
        hideProcessing();

        showToast(`Generated ${state.questions.length} questions!`, 'success');
        completeStep('knowledge');
        enableStep('assessment');
        switchView('assessment');
        syncAssessmentViewLayout();
        renderQuestion();
    } catch (err) {
        hideProcessing();
        showToast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
        clearInterval(progressTimer);
        releaseExclusiveSidebar();
        clearAssessmentCountdown();
        if (btn) btn.disabled = false;
    }
}

function initAssessmentNav() {
    $('#btn-prev-q')?.addEventListener('click', () => {
        if (state.currentQuestion > 0) {
            state.currentQuestion--;
            renderQuestion();
        }
    });

    $('#btn-next-q')?.addEventListener('click', () => {
        if (state.currentQuestion < state.questions.length - 1) {
            state.currentQuestion++;
            renderQuestion();
        }
    });

    $('#btn-submit')?.addEventListener('click', submitAssessment);
}

function renderQuestion() {
    const q = state.questions[state.currentQuestion];
    if (!q) return;

    const total = state.questions.length;
    const current = state.currentQuestion + 1;

    // Update progress
    $('#q-current').textContent = current;
    $('#q-total').textContent = total;
    $('#assessment-progress-fill').style.width = `${(current / total) * 100}%`;

    // Type badges
    const mcqCount = state.questions.filter(q => q.question_type === 'mcq').length;
    const subjCount = state.questions.filter(q => q.question_type === 'subjective').length;
    const pracCount = state.questions.filter(q => q.question_type === 'practical').length;
    $('#type-badges').innerHTML = `
        <span class="type-badge mcq">MCQ: ${mcqCount}</span>
        <span class="type-badge subjective">Subjective: ${subjCount}</span>
        <span class="type-badge practical">Practical: ${pracCount}</span>
    `;

    // Navigation buttons
    $('#btn-prev-q').disabled = state.currentQuestion === 0;
    const isLast = state.currentQuestion === total - 1;
    if (isLast) {
        $('#btn-next-q').classList.add('hidden');
        $('#btn-submit').classList.remove('hidden');
    } else {
        $('#btn-next-q').classList.remove('hidden');
        $('#btn-submit').classList.add('hidden');
    }

    // Render question card
    const container = $('#question-container');
    container.innerHTML = '';

    const card = document.createElement('div');
    card.className = 'question-card';

    // Meta line
    card.innerHTML = `
        <div class="question-meta">
            <span class="q-type-badge ${q.question_type}">${formatType(q.question_type)}</span>
            <span class="q-difficulty">${q.difficulty || 'medium'}</span>
            ${q.context_hint ? `<span class="q-context-hint">${q.context_hint}</span>` : ''}
        </div>
        <div class="question-text">${current}. ${q.question_text}</div>
    `;

    // Answer area
    if (q.question_type === 'mcq') {
        const optionsDiv = document.createElement('div');
        optionsDiv.className = 'mcq-options';

        (q.options || []).forEach(opt => {
            const optEl = document.createElement('div');
            optEl.className = 'mcq-option';
            if (state.answers[q.question_id] === opt.label) {
                optEl.classList.add('selected');
            }

            optEl.innerHTML = `
                <div class="option-radio"></div>
                <span class="option-label">${opt.label})</span>
                <span class="option-text">${opt.text}</span>
            `;

            optEl.addEventListener('click', () => {
                state.answers[q.question_id] = opt.label;
                optionsDiv.querySelectorAll('.mcq-option').forEach(o => o.classList.remove('selected'));
                optEl.classList.add('selected');
            });

            optionsDiv.appendChild(optEl);
        });

        card.appendChild(optionsDiv);

    } else if (q.question_type === 'practical') {
        // Show task description
        if (q.task_description) {
            const taskDiv = document.createElement('div');
            taskDiv.className = 'task-description';
            taskDiv.innerHTML = `
                <h4>📋 Task Description</h4>
                <p>${q.task_description}</p>
                ${q.deliverables && q.deliverables.length > 0
                    ? `<h4 style="margin-top:12px;">📦 Deliverables</h4>
                       <ul class="deliverables-list">
                           ${q.deliverables.map(d => `<li>${d}</li>`).join('')}
                       </ul>`
                    : ''}
            `;
            card.appendChild(taskDiv);
        }

        const textarea = document.createElement('textarea');
        textarea.className = 'answer-textarea';
        textarea.placeholder = 'Describe your approach and solution here...';
        textarea.value = state.answers[q.question_id] || '';
        textarea.addEventListener('input', () => {
            state.answers[q.question_id] = textarea.value;
        });
        card.appendChild(textarea);

    } else {
        // Subjective
        const textarea = document.createElement('textarea');
        textarea.className = 'answer-textarea';
        textarea.placeholder = 'Write your answer here...';
        textarea.value = state.answers[q.question_id] || '';
        textarea.addEventListener('input', () => {
            state.answers[q.question_id] = textarea.value;
        });
        card.appendChild(textarea);
    }

    container.appendChild(card);
}

async function submitAssessment() {
    // Validate all answered
    const unanswered = state.questions.filter(q => !state.answers[q.question_id]);
    if (unanswered.length > 0) {
        const proceed = confirm(
            `You have ${unanswered.length} unanswered question(s). Submit anyway?`
        );
        if (!proceed) return;
    }

    const btn = $('#btn-submit');
    if (btn) btn.disabled = true;
    if (btn)
        btn.innerHTML = '<div class="spinner" style="width:20px;height:20px;border-width:2px;"></div> Evaluating...';

    // Show loading overlay
    showLoadingOverlay('Evaluating your answers with AI...');
    beginExclusiveSidebar('assessment');

    try {
        const answers = state.questions.map(q => ({
            question_id: q.question_id,
            answer: state.answers[q.question_id] || '',
        }));

        const res = await apiFetch(`/assessment/${state.sessionId}/evaluate`, {
            method: 'POST',
            body: JSON.stringify({ answers }),
        });

        if (!res.ok) {
            throw new Error(await parseErrorResponse(res));
        }

        state.results = await res.json();
        hideLoadingOverlay();

        clearAssessmentCountdown();

        completeStep('assessment');
        enableStep('results');
        switchView('results');
        renderResults();

        showToast('Assessment evaluated!', 'success');

    } catch (err) {
        hideLoadingOverlay();
        showToast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
        releaseExclusiveSidebar();
        if (btn) btn.disabled = false;
        if (btn)
            btn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
            Submit Assessment
        `;
    }
}

// ── Results & admin evaluation viewer ────────────────────────────────
/** Render full evaluation report into dashboard + details containers */
function renderEvaluationReport(dashboardEl, detailsEl, r, questionsList, answersMap) {
    if (!dashboardEl || !detailsEl || !r) return;

    const pct = Number(r.percentage) || 0;
    const maxScoreDisplay =
        typeof r.max_total_score === 'number' && r.max_total_score > 0
            ? r.max_total_score
            : Array.isArray(questionsList) && questionsList.length > 0
              ? questionsList.length * 10
              : assessmentRuntime.questionCount * 10;
    const circumference = 2 * Math.PI * 65;
    const dashOffset = circumference * (1 - pct / 100);

    let strokeColor = '#ef4444';
    let readinessClass = 'insufficient';
    if (pct >= 90) {
        strokeColor = '#10b981';
        readinessClass = 'expert';
    } else if (pct >= 75) {
        strokeColor = '#3b82f6';
        readinessClass = 'proficient';
    } else if (pct >= 60) {
        strokeColor = '#f59e0b';
        readinessClass = 'developing';
    } else if (pct >= 40) {
        strokeColor = '#ef4444';
        readinessClass = 'needs-improvement';
    }

    const qById = {};
    (questionsList || []).forEach((q) => {
        qById[q.question_id] = q;
    });
    const am = answersMap || null;

    const strongAreas = r.strong_areas || ['None identified'];
    const weakAreas = r.weak_areas || ['None identified'];

    dashboardEl.innerHTML = `
        <div class="score-hero">
            <div class="score-circle">
                <svg viewBox="0 0 140 140">
                    <circle class="bg" cx="70" cy="70" r="65"/>
                    <circle class="fill" cx="70" cy="70" r="65"
                        stroke="${strokeColor}"
                        stroke-dasharray="${circumference}"
                        stroke-dashoffset="${circumference}"
                        data-target="${dashOffset}"/>
                </svg>
                <div class="score-value">
                    <span class="score-pct" style="color:${strokeColor}">${pct.toFixed(1)}%</span>
                    <span class="score-label">${Number(r.total_score || 0).toFixed(1)} / ${maxScoreDisplay.toFixed(1)}</span>
                </div>
            </div>
            <div class="readiness-badge ${readinessClass}">${escapeHtml(r.readiness_level || 'N/A')}</div>
        </div>

        <div class="score-category">
            <div class="cat-label">MCQ Score</div>
            <div class="cat-score" style="color: var(--mcq-color)">${Number(r.mcq_score || 0).toFixed(1)}</div>
        </div>
        <div class="score-category">
            <div class="cat-label">Subjective Score</div>
            <div class="cat-score" style="color: var(--subjective-color)">${Number(r.subjective_score || 0).toFixed(1)}</div>
        </div>

        <div class="score-category">
            <div class="cat-label">Practical Score</div>
            <div class="cat-score" style="color: var(--practical-color)">${Number(r.practical_score || 0).toFixed(1)}</div>
        </div>
        <div class="score-category">
            <div class="cat-label">Questions Answered</div>
            <div class="cat-score">${(r.evaluations || []).length}</div>
        </div>

        <div class="feedback-card">
            <h3>Overall feedback</h3>
            <div class="feedback-text">${escapeHtml(r.overall_feedback || 'No feedback available.')}</div>
            <div class="areas-grid">
                <div class="area-section strong">
                    <h4>Strong areas</h4>
                    <ul>${strongAreas.map((a) => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
                </div>
                <div class="area-section weak">
                    <h4>Areas to improve</h4>
                    <ul>${weakAreas.map((a) => `<li>${escapeHtml(a)}</li>`).join('')}</ul>
                </div>
            </div>
        </div>
    `;

    requestAnimationFrame(() => {
        const fillCircle = dashboardEl.querySelector('.fill');
        if (fillCircle) fillCircle.style.strokeDashoffset = fillCircle.dataset.target;
    });

    detailsEl.innerHTML = '<h3>Question-level detail</h3>';

    (r.evaluations || []).forEach((ev) => {
        const q = qById[ev.question_id];
        const scoreClass = ev.score >= 8 ? 'correct' : ev.score >= 5 ? 'partial' : 'wrong';
        const traineeRaw = am ? am[ev.question_id] : null;
        const answerLabel = am ? 'Your answer' : 'Trainee answer';
        let traineeBlock;
        if (am) {
            traineeBlock =
                traineeRaw !== null && traineeRaw !== undefined && String(traineeRaw).trim() !== ''
                    ? escapeHtml(String(traineeRaw))
                    : '<em>No answer provided</em>';
        } else {
            traineeBlock =
                '<em class="muted">Trainee response is not retained on the server for this assignment. Refer to scoring and rationale.</em>';
        }

        const item = document.createElement('div');
        item.className = 'result-item';
        item.innerHTML = `
            <div class="result-header">
                <div class="result-q-num ${scoreClass}">Q${escapeHtml(ev.question_id)}</div>
                <div class="result-q-text">${escapeHtml(q?.question_text || 'Question')}</div>
                <div class="result-score" style="color: ${scoreClass === 'correct' ? 'var(--accent-green)' : scoreClass === 'partial' ? 'var(--accent-orange)' : 'var(--accent-red)'}">${Number(ev.score).toFixed(1)}/10</div>
            </div>
            <div class="result-expanded">
                <div class="result-section">
                    <strong>${answerLabel}</strong>
                    ${traineeBlock}
                </div>
                <div class="result-section">
                    <strong>Expected / keyed answer</strong>
                    ${escapeHtml(ev.correct_answer || 'N/A')}
                </div>
                <div class="result-section">
                    <strong>Explanation</strong>
                    ${escapeHtml(ev.explanation || 'N/A')}
                </div>
                ${
                    ev.improvement_suggestions
                        ? `<div class="result-section"><strong>Suggestions</strong><p class="muted-p">${escapeHtml(ev.improvement_suggestions)}</p></div>`
                        : ''
                }
            </div>
        `;

        item.addEventListener('click', () => item.classList.toggle('expanded'));
        detailsEl.appendChild(item);
    });
}

function renderResults() {
    const r = state.results;
    if (!r) return;
    renderEvaluationReport($('#results-dashboard'), $('#results-details'), r, state.questions || [], state.answers || {});
}

async function openAdminEvaluationDetail(row) {
    const r = row?.evaluation;
    if (!r || !(r.evaluations || []).length) {
        showToast('No evaluation payload for this submission', 'error');
        return;
    }
    const meta = $('#admin-eval-meta');
    if (meta) {
        const fn = row.trainee_full_name ? ` (${row.trainee_full_name})` : '';
        meta.textContent = `${row.trainee_username || '?'}${fn} · Session ${row.session_id || '?'} · Submitted ${row.submitted_at || '—'}`;
    }

    let questionsList = [];
    if (row.session_id) {
        try {
            const qRes = await apiFetch(`/assessment/${encodeURIComponent(row.session_id)}/questions`, { method: 'GET' });
            if (qRes.ok) {
                const jd = await qRes.json();
                questionsList = jd.questions || [];
            }
        } catch {
            /* ignore */
        }
    }

    renderEvaluationReport($('#admin-eval-dashboard'), $('#admin-eval-details'), r, questionsList, null);

    const dlg = $('#modal-admin-evaluation');
    if (dlg?.showModal) dlg.showModal();
}

function wireAdminEvaluationModal() {
    const tb = $('#admin-results-body');
    if (!tb || tb.dataset.evalLinksWired === '1') return;
    tb.dataset.evalLinksWired = '1';
    tb.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.btn-link-eval');
        if (!btn) return;
        ev.preventDefault();
        const aid = Number(btn.dataset.aid);
        const row = adminEvaluationRowById.get(aid);
        if (row) openAdminEvaluationDetail(row);
    });

    $('#btn-admin-eval-close')?.addEventListener('click', () => $('#modal-admin-evaluation')?.close());
}

// ── UI Helpers ──────────────────────────────────────────────────────
function formatType(type) {
    const map = { mcq: 'MCQ', subjective: 'Subjective', practical: 'Practical' };
    return map[type] || type;
}

function showProcessing(title, message) {
    const el = $('#processing-status');
    updateProgress(0);
    el.classList.remove('hidden');
    updateProcessingText(title, message);
}

function hideProcessing() {
    $('#processing-status').classList.add('hidden');
}

function updateProcessingText(title, message) {
    $('#status-title').textContent = title;
    $('#status-message').textContent = message;
}

function updateProgress(pct) {
    $('#progress-fill').style.width = `${pct}%`;
}

function showLoadingOverlay(text) {
    const overlay = document.createElement('div');
    overlay.className = 'loading-overlay';
    overlay.id = 'loading-overlay';
    overlay.innerHTML = `<div class="spinner"></div><p>${text}</p>`;
    document.body.appendChild(overlay);
}

function hideLoadingOverlay() {
    const overlay = $('#loading-overlay');
    if (overlay) overlay.remove();
}

function showToast(message, type = 'info', ttlMs) {
    const ttl = ttlMs ?? (type === 'error' ? 10000 : 4500);
    const container = $('#toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icons = {
        success: '✓',
        error: '✗',
        info: 'ℹ',
    };
    toast.innerHTML = `<span>${icons[type] || 'ℹ'}</span> ${message}`;
    container.appendChild(toast);

    setTimeout(() => toast.remove(), ttl);
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
