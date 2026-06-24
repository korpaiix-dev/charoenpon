// ===== Theme toggle (light/dark) =====
function applyTheme(theme) {
    const t = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('theme', t); } catch {}
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) btn.textContent = t === 'dark' ? '☀️' : '🌙';
}
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    applyTheme(current === 'light' ? 'dark' : 'light');
}
// Init on page load
(function() {
    let saved = null;
    try { saved = localStorage.getItem('theme'); } catch {}
    applyTheme(saved || 'light');
})();

/* ============================================
   เจริญพร Dashboard — SPA Application
   ============================================ */

// ========== STATE ==========
let token = localStorage.getItem('token');
let admin = JSON.parse(localStorage.getItem('admin') || 'null');
let currentPage = 'dashboard';
let charts = {};

const ROLE_LEVELS = { owner: 100, super_admin: 75, admin: 50, moderator: 10 };

// FIX 2025-05-21 (Phase D-XSS): HTML escape helper — ใช้ทุกที่ที่ใส่ user/DB string เข้า innerHTML
function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// FIX 2025-05-21 (Phase D-XSS): Double-submit guard helpers
const _busy = new Set();
async function withBusy(key, fn) {
    if (_busy.has(key)) return;
    _busy.add(key);
    try { await fn(); }
    finally { _busy.delete(key); }
}


const NAV_ITEMS = [
    { id: 'dashboard', icon: '📊', label: 'ภาพรวม', minRole: 'moderator' },
    { id: 'customers', icon: '👥', label: 'ลูกค้า', minRole: 'moderator' },
    { id: 'finance', icon: '💰', label: 'การเงิน', minRole: 'moderator' },
    { id: 'promotions', icon: '📢', label: 'โปรโมชั่น', minRole: 'admin' },
    { id: 'content', icon: '📸', label: 'Content', minRole: 'moderator' },
    { id: 'groups', icon: '📱', label: 'กลุ่ม', minRole: 'admin' },
    { id: 'team', icon: '👨‍💼', label: 'ทีมงาน', minRole: 'admin' },
    { id: 'settings', icon: '⚙️', label: 'ตั้งค่า', minRole: 'admin' },
    { id: 'marketing', icon: '📊', label: 'Marketing', minRole: 'admin' },
    { id: 'activity', icon: '📋', label: 'Activity Log', minRole: 'admin' },
];

// ========== API ==========
let _loggingOut = false;
async function api(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    
    const resp = await fetch(`/api${path}`, { ...options, headers: { ...headers, ...options.headers } });
    if (resp.status === 401) {
        if (!_loggingOut) { _loggingOut = true; logout(); _loggingOut = false; }
        throw new Error('Session expired');
    }
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Error' }));
        throw new Error(err.detail || 'API Error');
    }
    return resp.json();
}

// ========== AUTH ==========
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';
    try {
        const data = await api('/auth/login', {
            method: 'POST',
            body: JSON.stringify({
                telegram_id: parseInt(document.getElementById('login-tid').value),
                password: document.getElementById('login-pwd').value,
            }),
        });
        token = data.token;
        admin = data.admin;
        localStorage.setItem('token', token);
        localStorage.setItem('admin', JSON.stringify(admin));
        try { showApp(); } catch(e) { console.error('showApp error:', e); alert('Login สำเร็จแต่โหลดหน้าไม่ได้: ' + e.message); }
    } catch (err) {
        console.error('Login error:', err);
        errEl.textContent = err.message || 'เข้าสู่ระบบไม่สำเร็จ';
    }
});

function togglePassword() {
    const inp = document.getElementById('login-pwd');
    inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function logout() {
    // FIX 2025-05-21 (Phase D-XSS): full cleanup — flush LS/SS, kill timers, destroy charts
    if (token) {
        try { await fetch('/api/auth/logout', { method:'POST', headers:{Authorization:`Bearer ${token}`} }); } catch {}
    }
    token = null; admin = null;
    ['token','admin','access_token'].forEach(k => localStorage.removeItem(k));
    try { sessionStorage.clear(); } catch {}
    if (alertInterval) { clearInterval(alertInterval); alertInterval = null; }
    try { Object.values(charts).forEach(c => c && c.destroy && c.destroy()); } catch {}
    charts = {};
    lastAlertCount = { pending: -1, sos: -1 };
    document.getElementById('login-page').classList.remove('hidden');
    document.getElementById('app').classList.add('hidden');
}

// ========== APP INIT ==========
function showApp() {
    document.getElementById('login-page').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    renderSidebar();
    navigate('dashboard');
    startAlertPolling();
}

function renderSidebar() {
    const nav = document.getElementById('sidebar-nav');
    const level = ROLE_LEVELS[admin.role] || 0;
    nav.innerHTML = NAV_ITEMS
        .filter(item => level >= ROLE_LEVELS[item.minRole])
        .map(item => `<div class="nav-item ${item.id === currentPage ? 'active' : ''}" onclick="navigate('${item.id}')">
            <span class="nav-icon">${item.icon}</span> ${item.label}
        </div>`).join('');
    
    document.getElementById('sidebar-user-name').textContent = admin.display_name;
    const roleLabels = { owner: '👑 Owner', super_admin: '⚡ Super Admin', admin: '🛡️ Admin', moderator: '📋 Moderator' };
    document.getElementById('sidebar-user-role').textContent = roleLabels[admin.role] || admin.role;
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ========== NAVIGATION ==========
function navigate(page) {
    currentPage = page;
    renderSidebar();
    const titles = {
        dashboard: '📊 ภาพรวม', customers: '👥 ลูกค้า', finance: '💰 การเงิน',
        promotions: '📢 โปรโมชั่น', content: '📸 Content', groups: '📱 กลุ่ม',
        team: '👨‍💼 ทีมงาน', settings: '⚙️ ตั้งค่า', marketing: '📊 Marketing',
        activity: '📋 Activity Log',
    };
    document.getElementById('page-title').textContent = titles[page] || page;
    document.getElementById('sidebar').classList.remove('open');
    
    // Destroy old charts
    Object.values(charts).forEach(c => c.destroy && c.destroy());
    charts = {};
    
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    
    const pages = {
        dashboard: renderDashboard, customers: renderCustomers, finance: renderFinance,
        promotions: renderPromotions, content: renderContent, groups: renderGroups,
        team: renderTeam, settings: renderSettings, marketing: renderMarketing,
        activity: renderActivityLog,
    };
    (pages[page] || (() => { content.innerHTML = '<div class="empty-state"><div class="icon">🚧</div><p>Coming soon</p></div>'; }))();
}

// ========== TOAST ==========
function toast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 5000);
}

// ========== MODAL ==========
function openModal(title, bodyHtml) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal(e) {
    if (e && e.target !== document.getElementById('modal-overlay')) return;
    document.getElementById('modal-overlay').classList.add('hidden');
}

// ========== ALERT POLLING + BROWSER NOTIFICATION + SOUND ==========
let alertInterval;
let lastAlertCount = { pending: -1, sos: -1 };
let notifSound = null;

function initNotifSound() {
    // สร้างเสียงเตือนด้วย Web Audio API (ไม่ต้องโหลดไฟล์)
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        notifSound = new AudioCtx();
    } catch {}
}

function playNotifSound(type) {
    try {
        if (!notifSound || notifSound.state === 'closed') initNotifSound();
        if (notifSound.state === 'suspended') notifSound.resume();
        const osc = notifSound.createOscillator();
        const gain = notifSound.createGain();
        osc.connect(gain);
        gain.connect(notifSound.destination);
        gain.gain.value = 0.3;
        if (type === 'sos') {
            // SOS = เสียงด่วน 3 ครั้ง
            osc.frequency.value = 880;
            osc.type = 'square';
            gain.gain.setValueAtTime(0.3, notifSound.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, notifSound.currentTime + 0.6);
            osc.start(notifSound.currentTime);
            osc.stop(notifSound.currentTime + 0.6);
        } else if (type === 'anomaly') {
            // ยอดผิดปกติ = เสียงต่ำเตือน
            osc.frequency.value = 440;
            osc.type = 'sawtooth';
            gain.gain.setValueAtTime(0.25, notifSound.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, notifSound.currentTime + 0.8);
            osc.start(notifSound.currentTime);
            osc.stop(notifSound.currentTime + 0.8);
        } else {
            // สลิปใหม่ = เสียงสั้นๆ
            osc.frequency.value = 660;
            osc.type = 'sine';
            gain.gain.setValueAtTime(0.3, notifSound.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, notifSound.currentTime + 0.4);
            osc.start(notifSound.currentTime);
            osc.stop(notifSound.currentTime + 0.4);
        }
    } catch {}
}

function startAlertPolling() {
    initNotifSound();
    checkAlerts();
    alertInterval = setInterval(checkAlerts, 10000); // 10 วินาที
}

// ── In-Page Notification (ทำงานบน HTTP ได้) ──
let _notifId = 0;
function showNotifCard(type, title, desc, imgUrl, onClick) {
    const panel = document.getElementById('notif-panel');
    if (!panel) return;
    const id = `notif-${++_notifId}`;
    const typeClass = type === 'sos' ? 'notif-sos' : type === 'anomaly' ? 'notif-anomaly' : 'notif-slip';
    const now = new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });

    let imgHtml;
    if (imgUrl) {
        imgHtml = `<img class="notif-img" src="${imgUrl}" onerror="this.outerHTML='<div class=\\'notif-noimg\\'>📄</div>'" alt="สลิป">`;
    } else {
        const emoji = type === 'sos' ? '🆘' : type === 'anomaly' ? '⚠️' : '💰';
        imgHtml = `<div class="notif-noimg">${emoji}</div>`;
    }

    const card = document.createElement('div');
    card.id = id;
    card.className = `notif-card ${typeClass}`;
    card.innerHTML = `
        ${imgHtml}
        <div class="notif-body">
            <div class="notif-title"><span class="notif-dot"></span>${title}</div>
            <div class="notif-desc">${desc}</div>
            <div class="notif-time">${now}</div>
        </div>
        <button class="notif-close" onclick="event.stopPropagation();dismissNotif('${id}')">&times;</button>
    `;
    card.addEventListener('click', () => {
        dismissNotif(id);
        if (onClick) onClick();
    });
    panel.prepend(card);

    // Auto-dismiss หลัง 30 วินาที (SOS ไม่หาย ต้องกดปิดเอง)
    if (type !== 'sos') {
        setTimeout(() => dismissNotif(id), 30000);
    }

    // จำกัดไม่เกิน 5 การ์ด
    while (panel.children.length > 5) {
        panel.removeChild(panel.lastChild);
    }
}

function dismissNotif(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.add('notif-hide');
    setTimeout(() => el.remove(), 300);
}

async function checkAlerts() {
    try {
        // Auto-refresh pending slips + SOS on dashboard page
        if (currentPage === 'dashboard') {
            loadDashboardPendingSlips();
            loadSOSAlerts();
        }
        if (currentPage === 'finance') {
            loadPendingSlips();
        }
        const data = await api('/dashboard/alerts');
        const pendingCount = data.pending_slips || 0;
        const sosCount = data.sos_count || 0;
        const anomalyCount = data.anomaly_count || 0;
        const totalBadge = pendingCount + sosCount + anomalyCount;

        // Update badge
        const badge = document.getElementById('alert-badge');
        const countEl = document.getElementById('alert-count');
        if (totalBadge > 0) {
            badge.classList.remove('hidden');
            countEl.textContent = totalBadge;
        } else {
            badge.classList.add('hidden');
        }

        // In-Page Notification + Sound (skip first load when counts are -1)
        if (lastAlertCount.pending >= 0) {
            // 🔔 สลิปใหม่ — พร้อมรูป
            if ((data.pending_payments || 0) > lastAlertCount.pending) {
                const newSlips = data.new_slips || [];
                if (newSlips.length > 0) {
                    newSlips.slice(0, 3).forEach(slip => {
                        const imgUrl = slip.slip_file_id ? `/api/payments/${slip.id}/slip-image` : null;
                        const name = slip.first_name || slip.username || 'ลูกค้า';
                        const amt = Number(slip.amount).toLocaleString();
                        showNotifCard('slip',
                            '💰 สลิปใหม่รอตรวจ!',
                            `<b>${esc(name)}</b> — ฿${esc(amt)}<br>${esc(slip.package_name || '')}`,
                            imgUrl,
                            () => navigate('finance')
                        );
                    });
                } else {
                    showNotifCard('slip',
                        '💰 สลิปใหม่รอตรวจ!',
                        `มีสลิปใหม่ <b>${data.pending_payments}</b> รายการ`,
                        null,
                        () => navigate('finance')
                    );
                }
                playNotifSound('slip');
            }
            // 🆘 SOS
            if (sosCount > lastAlertCount.sos) {
                showNotifCard('sos',
                    '🆘 SOS — ด่วน!',
                    `ลูกค้าแจ้งปัญหา <b>${sosCount}</b> ราย<br>กรุณาตรวจสอบทันที!`,
                    null,
                    () => navigate('dashboard')
                );
                playNotifSound('sos');
            }
            // ⚠️ ยอดผิดปกติ
            if (anomalyCount > (lastAlertCount.anomaly || 0)) {
                const anomalies = data.anomalies || [];
                const desc = anomalies.length > 0
                    ? anomalies.slice(0, 2).map(a => `<b>${esc(a.first_name || 'ลูกค้า')}</b>: ฿${Number(a.amount).toLocaleString()} (${esc(a.reason)})`).join('<br>')
                    : `พบยอดผิดปกติ <b>${anomalyCount}</b> รายการ`;
                showNotifCard('anomaly',
                    '⚠️ ยอดเงินผิดปกติ!',
                    desc,
                    null,
                    () => navigate('finance')
                );
                playNotifSound('anomaly');
            }
        }

        lastAlertCount = { pending: data.pending_payments || 0, sos: sosCount, anomaly: anomalyCount };
    } catch {}
}

// ========== HELPERS ==========
function fmt(n) {
    if (n === null || n === undefined) return '-';
    return new Intl.NumberFormat('th-TH').format(n);
}
function fmtBaht(n) { return '฿' + fmt(n); }
function fmtDate(d) {
    if (!d) return '-';
    const dt = new Date(d);
    return dt.toLocaleDateString('th-TH', { day: '2-digit', month: '2-digit', year: '2-digit' });
}
function fmtDateTime(d) {
    if (!d) return '-';
    const dt = new Date(d);
    return dt.toLocaleDateString('th-TH', { day: '2-digit', month: '2-digit' }) + ' ' +
           dt.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
}
function changeArrow(pct) {
    if (pct > 0) return `<span class="card-change up">+${pct}% ▲</span>`;
    if (pct < 0) return `<span class="card-change down">${pct}% ▼</span>`;
    return `<span class="card-change">0%</span>`;
}
function statusBadge(status) {
    const s = (status || '').toLowerCase();
    return `<span class="status-badge status-${s}">${status}</span>`;
}
function hasRole(minRole) { return (ROLE_LEVELS[admin.role] || 0) >= (ROLE_LEVELS[minRole] || 999); }
function isoDate(d) {
    const dt = new Date(d);
    dt.setMinutes(dt.getMinutes() - dt.getTimezoneOffset());
    return dt.toISOString().slice(0, 10);
}
function isoMonth(d) { return isoDate(d).slice(0, 7); }
function thRange(from, to) {
    if (from === to) return fmtDate(from);
    return `${fmtDate(from)} - ${fmtDate(to)}`;
}
let dashboardPeriod = 'month';
let dashboardDateFrom = isoDate(new Date());
let dashboardDateTo = isoDate(new Date());
let dashboardMonth = isoMonth(new Date());

function setDashboardQuick(type) {
    const now = new Date();
    if (type === 'today') {
        dashboardPeriod = 'day'; dashboardDateFrom = isoDate(now); dashboardDateTo = dashboardDateFrom;
    } else if (type === 'yesterday') {
        const y = new Date(now); y.setDate(y.getDate() - 1);
        dashboardPeriod = 'day'; dashboardDateFrom = isoDate(y); dashboardDateTo = dashboardDateFrom;
    } else if (type === 'this-month') {
        dashboardPeriod = 'month'; dashboardMonth = isoMonth(now);
    } else if (type === 'last-month') {
        const m = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        dashboardPeriod = 'month'; dashboardMonth = isoMonth(m);
    }
    renderDashboard();
}

function dashboardPeriodChanged(value) {
    dashboardPeriod = value;
    const monthGroup = document.getElementById('dashboard-month-group');
    const rangeGroup = document.getElementById('dashboard-range-group');
    if (monthGroup) monthGroup.classList.toggle('hidden', value !== 'month');
    if (rangeGroup) rangeGroup.classList.toggle('hidden', value === 'month');
}

function applyDashboardAnalytics() {
    dashboardPeriod = document.getElementById('dashboard-period')?.value || dashboardPeriod;
    dashboardMonth = document.getElementById('dashboard-month')?.value || dashboardMonth;
    dashboardDateFrom = document.getElementById('dashboard-date-from')?.value || dashboardDateFrom;
    dashboardDateTo = document.getElementById('dashboard-date-to')?.value || dashboardDateFrom;
    if (dashboardPeriod === 'day') dashboardDateTo = dashboardDateFrom;
    renderDashboard();
}
function paginationHtml(page, pages, fn) {
    if (pages <= 1) return '';
    let html = '<div class="pagination">';
    if (page > 1) html += `<button onclick="${fn}(${page - 1})">◀</button>`;
    for (let i = Math.max(1, page - 2); i <= Math.min(pages, page + 2); i++) {
        html += `<button class="${i === page ? 'active' : ''}" onclick="${fn}(${i})">${i}</button>`;
    }
    if (page < pages) html += `<button onclick="${fn}(${page + 1})">▶</button>`;
    html += '</div>';
    return html;
}

// ========== PAGE: DASHBOARD ==========
async function renderDashboard() {
    const content = document.getElementById('page-content');
    try {
        const analyticsParams = dashboardPeriod === 'month'
            ? `period=month&date_from=${encodeURIComponent(dashboardMonth)}`
            : dashboardPeriod === 'day'
                ? `period=day&date_from=${encodeURIComponent(dashboardDateFrom)}`
                : `period=custom&date_from=${encodeURIComponent(dashboardDateFrom)}&date_to=${encodeURIComponent(dashboardDateTo)}`;

        const [summary, members, flashSale, alerts, analytics, revSummary] = await Promise.all([
            api('/dashboard/summary'),
            api('/dashboard/members-stats'),
            api('/dashboard/flash-sale-status'),
            api('/dashboard/alerts'),
            api(`/dashboard/sales-analytics?${analyticsParams}`),
            api('/dashboard/revenue-summary').catch(() => null),
        ]);
        dashboardDateFrom = analytics.date_from;
        dashboardDateTo = analytics.date_to;
        dashboardMonth = analytics.date_from.slice(0, 7);
        
        let dmHtml = '', contentHtml = '';
        if (hasRole('admin')) {
            try {
                const [dm, cs] = await Promise.all([api('/dashboard/dm-stats'), api('/dashboard/content-stats')]);
                dmHtml = `
                    <div class="card"><div class="card-label">📨 COMEBACK DM</div>
                        <div class="detail-row"><span class="detail-label">ส่ง</span><span class="detail-value">${dm.comeback_sent}</span></div>
                        <div class="detail-row"><span class="detail-label">ตอบ</span><span class="detail-value">${dm.comeback_respond}</span></div>
                        <div class="detail-row"><span class="detail-label">สมัคร</span><span class="detail-value">${dm.comeback_convert}</span></div>
                    </div>
                    <div class="card"><div class="card-label">🎯 Trial DM</div>
                        <div class="detail-row"><span class="detail-label">ส่ง</span><span class="detail-value">${dm.trial_sent}</span></div>
                        <div class="detail-row"><span class="detail-label">คลิก</span><span class="detail-value">${dm.trial_click}</span></div>
                        <div class="detail-row"><span class="detail-label">สมัคร</span><span class="detail-value">${dm.trial_convert}</span></div>
                    </div>`;
                contentHtml = `
                    <div class="card"><div class="card-label">📸 Content Bot</div>
                        <div class="detail-row"><span class="detail-label">Teaser ส่งวันนี้</span><span class="detail-value">${cs.teasers_sent_today}</span></div>
                        <div class="detail-row"><span class="detail-label">คลิกวันนี้</span><span class="detail-value">${cs.teaser_clicks_today}</span></div>
                        <div class="detail-row"><span class="detail-label">Queue คงเหลือ</span><span class="detail-value">${cs.queue_remaining} รูป</span></div>
                    </div>`;
            } catch {}
        }

        const flashHtml = flashSale.active 
            ? `<div class="card"><div class="card-label">⚡ Flash Sale</div>
                <div style="font-size:0.9rem;color:var(--success);">● เปิดอยู่</div>
                <div class="card-value">${flashSale.sold_slots}/${flashSale.total_slots}</div>
                <div style="font-size:0.8rem;color:var(--text-muted);">${flashSale.name}</div></div>`
            : `<div class="card"><div class="card-label">⚡ Flash Sale</div><div style="color:var(--text-dim);">ไม่มี sale ตอนนี้</div></div>`;

        let alertItems = '';
        if (alerts.pending_slips > 0) alertItems += `<div class="alert-box-item">⏳ ${alerts.pending_slips} สลิปรอ approve</div>`;
        if (alerts.expiring_today > 0) alertItems += `<div class="alert-box-item">🔔 ${alerts.expiring_today} สมาชิกหมดอายุวันนี้</div>`;
        if ((alerts.sos_count || 0) > 0) alertItems += `<div class="alert-box-item">🆘 ${alerts.sos_count} SOS แจ้งปัญหา</div>`;
        if (!alertItems) alertItems = '<div class="alert-box-item" style="color:var(--success);">✅ ไม่มี alert</div>';

        const packageRows = analytics.packages.length
            ? analytics.packages.map(p => `<tr><td>${esc(p.package_name)}</td><td>${fmtBaht(p.revenue)}</td><td>${fmt(p.buyers)}</td><td>${fmt(p.orders)}</td></tr>`).join('')
            : `<tr><td colspan="4" style="color:var(--text-muted);text-align:center;">ไม่มียอดขายในช่วงนี้</td></tr>`;
        const monthRows = analytics.months.length
            ? analytics.months.map(m => `<tr><td>${esc(m.month)}</td><td>${fmtBaht(m.revenue)}</td><td>${fmt(m.buyers)}</td><td>${fmt(m.orders)}</td></tr>`).join('')
            : `<tr><td colspan="4" style="color:var(--text-muted);text-align:center;">ยังไม่มีข้อมูลรายเดือน</td></tr>`;

        // Total Revenue Summary card row
        const revHtml = revSummary ? `
            <div class="rev-summary-row" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));gap:0.75rem;margin-bottom:1.5rem;">
                <div class="rev-card today">
                    <div class="rev-label">📅 วันนี้</div>
                    <div class="rev-value">${fmtBaht(revSummary.today.amount)}</div>
                    <div class="rev-sub">${fmt(revSummary.today.count)} order${revSummary.today.vs_yesterday_pct != null ? ` · ${revSummary.today.vs_yesterday_pct >= 0 ? '▲' : '▼'} ${Math.abs(revSummary.today.vs_yesterday_pct)}%` : ''}</div>
                </div>
                <div class="rev-card month">
                    <div class="rev-label">📆 เดือนนี้</div>
                    <div class="rev-value">${fmtBaht(revSummary.this_month.amount)}</div>
                    <div class="rev-sub">${fmt(revSummary.this_month.count)} order${revSummary.this_month.vs_last_month_pct != null ? ` · ${revSummary.this_month.vs_last_month_pct >= 0 ? '▲' : '▼'} ${Math.abs(revSummary.this_month.vs_last_month_pct)}%` : ''}</div>
                </div>
                <div class="rev-card year">
                    <div class="rev-label">📊 ปีนี้</div>
                    <div class="rev-value">${fmtBaht(revSummary.this_year.amount)}</div>
                    <div class="rev-sub">${fmt(revSummary.this_year.count)} order${revSummary.this_year.vs_last_year_pct != null ? ` · ${revSummary.this_year.vs_last_year_pct >= 0 ? '▲' : '▼'} ${Math.abs(revSummary.this_year.vs_last_year_pct)}%` : ''}</div>
                </div>
                <div class="rev-card alltime">
                    <div class="rev-label">💎 รวมทั้งหมด</div>
                    <div class="rev-value">${fmtBaht(revSummary.all_time.amount)}</div>
                    <div class="rev-sub">${fmt(revSummary.all_time.count)} order ตลอดอายุระบบ</div>
                </div>
            </div>` : '';

        content.innerHTML = `
            ${revHtml}
            <div class="dashboard-hero">
                <div>
                    <div class="hero-kicker">ภาพรวมยอดขายย้อนหลัง</div>
                    <div class="hero-title">${thRange(analytics.date_from, analytics.date_to)}</div>
                    <div class="hero-subtitle">เทียบกับช่วงก่อนหน้า ${thRange(analytics.previous_from, analytics.previous_to)}</div>
                </div>
                <div class="dashboard-filter-panel">
                    <div class="quick-filters">
                        <button class="filter-btn ${dashboardPeriod === 'day' && dashboardDateFrom === isoDate(new Date()) ? 'active' : ''}" onclick="setDashboardQuick('today')">วันนี้</button>
                        <button class="filter-btn" onclick="setDashboardQuick('yesterday')">เมื่อวาน</button>
                        <button class="filter-btn ${dashboardPeriod === 'month' && dashboardMonth === isoMonth(new Date()) ? 'active' : ''}" onclick="setDashboardQuick('this-month')">เดือนนี้</button>
                        <button class="filter-btn" onclick="setDashboardQuick('last-month')">เดือนที่แล้ว</button>
                    </div>
                    <div class="filters dashboard-filters">
                        <select id="dashboard-period" onchange="dashboardPeriodChanged(this.value)">
                            <option value="day" ${dashboardPeriod === 'day' ? 'selected' : ''}>ดูรายวัน</option>
                            <option value="month" ${dashboardPeriod === 'month' ? 'selected' : ''}>ดูรายเดือน</option>
                            <option value="custom" ${dashboardPeriod === 'custom' ? 'selected' : ''}>เลือกช่วงเอง</option>
                        </select>
                        <div id="dashboard-month-group" class="filter-inline ${dashboardPeriod !== 'month' ? 'hidden' : ''}"><input type="month" id="dashboard-month" value="${dashboardMonth}"></div>
                        <div id="dashboard-range-group" class="filter-inline ${dashboardPeriod === 'month' ? 'hidden' : ''}">
                            <input type="date" id="dashboard-date-from" value="${dashboardDateFrom}">
                            <input type="date" id="dashboard-date-to" value="${dashboardDateTo}">
                        </div>
                        <button class="btn btn-primary" onclick="applyDashboardAnalytics()">ดูข้อมูล</button>
                    </div>
                </div>
            </div>

            <div class="cards-grid metric-grid">
                <div class="card metric-card primary"><div class="card-label">รายได้ช่วงที่เลือก</div><div class="card-value">${fmtBaht(analytics.summary.revenue)}</div>${changeArrow(analytics.summary.revenue_change)}</div>
                <div class="card metric-card success"><div class="card-label">ลูกค้าที่ซื้อ</div><div class="card-value">${fmt(analytics.summary.buyers)} คน</div>${changeArrow(analytics.summary.buyers_change)}</div>
                <div class="card metric-card"><div class="card-label">ออเดอร์ทั้งหมด</div><div class="card-value">${fmt(analytics.summary.orders)}</div><div class="card-change">เฉลี่ย ${fmtBaht(Math.round(analytics.summary.avg_order))}/ออเดอร์</div></div>
                <div class="card metric-card"><div class="card-label">ลูกค้าใหม่ที่ซื้อครั้งแรก</div><div class="card-value">${fmt(analytics.summary.new_buyers)} คน</div><div class="card-change">นับจากยอด CONFIRMED</div></div>
            </div>

            <div class="cards-grid compact-overview">
                <div class="card"><div class="card-label">วันนี้</div><div class="card-value">${fmtBaht(summary.today)}</div>${changeArrow(summary.today_change)}</div>
                <div class="card"><div class="card-label">สัปดาห์นี้</div><div class="card-value">${fmtBaht(summary.week)}</div>${changeArrow(summary.week_change)}</div>
                <div class="card"><div class="card-label">เดือนนี้</div><div class="card-value">${fmtBaht(summary.month)}</div>${changeArrow(summary.month_change)}</div>
            </div>

            <div class="dashboard-grid-2">
                <div class="card card-full"><div class="card-label">📈 รายได้ + จำนวนลูกค้าตามวันที่เลือก</div><div class="chart-container chart-tall"><canvas id="sales-analytics-chart"></canvas></div></div>
                <div class="card card-full"><div class="card-label">📦 แพ็กเกจขายดีในช่วงนี้</div><div class="table-wrap"><table><thead><tr><th>แพ็กเกจ</th><th>รายได้</th><th>ลูกค้า</th><th>ออเดอร์</th></tr></thead><tbody>${packageRows}</tbody></table></div></div>
            </div>

            <div class="card card-full"><div class="card-label">🗓️ ยอดรายเดือนย้อนหลัง 12 เดือน</div><div class="table-wrap"><table><thead><tr><th>เดือน</th><th>รายได้</th><th>ลูกค้า</th><th>ออเดอร์</th></tr></thead><tbody>${monthRows}</tbody></table></div></div>

            <div class="cards-grid" style="margin-top:1rem;">
                <div class="card"><div class="card-label">Active Members</div><div class="card-value" style="color:var(--success);">${fmt(members.active)}</div></div>
                <div class="card"><div class="card-label">Expired</div><div class="card-value" style="color:var(--error);">${fmt(members.expired)}</div></div>
                <div class="card"><div class="card-label">สมาชิกใหม่วันนี้</div><div class="card-value" style="color:var(--primary);">${fmt(members.new_today)}</div></div>
                <div class="card"><div class="card-label">Total Users</div><div class="card-value">${fmt(members.total_users)}</div></div>
            </div>
            <div class="cards-grid" style="margin-top:1rem;">
                ${flashHtml} ${dmHtml} ${contentHtml}
            </div>
            <div class="section-title" style="margin-top:1.5rem;">🚨 Alerts</div>
            <div class="alert-box">${alertItems}</div>
            <div id="dashboard-pending-slips"></div>
            <div id="sos-section"></div>
        `;

        loadDashboardPendingSlips();
        loadSOSAlerts();
        
        const ctx = document.getElementById('sales-analytics-chart');
        if (ctx) {
            charts.salesAnalytics = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: analytics.chart.map(d => d.date.slice(5)),
                    datasets: [
                        {
                            type: 'bar',
                            label: 'รายได้ (฿)',
                            data: analytics.chart.map(d => d.revenue),
                            backgroundColor: 'rgba(247, 176, 69, 0.7)',
                            borderColor: '#f7b045',
                            borderWidth: 1,
                            yAxisID: 'y',
                        },
                        {
                            type: 'line',
                            label: 'ลูกค้าที่ซื้อ (คน)',
                            data: analytics.chart.map(d => d.buyers),
                            borderColor: '#4fd1c5',
                            backgroundColor: 'rgba(79, 209, 197, 0.18)',
                            tension: 0.35,
                            pointRadius: 3,
                            yAxisID: 'buyers',
                        }
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { ticks: { color: '#a8b3cf' }, grid: { color: 'rgba(60,72,107,0.22)' } },
                        y: { position: 'left', ticks: { color: '#a8b3cf', callback: v => '฿' + fmt(v) }, grid: { color: 'rgba(60,72,107,0.22)' } },
                        buyers: { position: 'right', ticks: { color: '#a8b3cf', callback: v => fmt(v) }, grid: { drawOnChartArea: false } },
                    },
                    plugins: { legend: { labels: { color: '#f5f1e8' } } },
                }
            });
        }
    } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

// ========== SOS ALERTS ==========
async function loadSOSAlerts() {
    const section = document.getElementById('sos-section');
    if (!section) return;
    try {
        const data = await api('/dashboard/sos-alerts');
        if (!data.length) {
            section.innerHTML = '';
            return;
        }
        let html = `<div class="section-title" style="margin-top:1.5rem;display:flex;align-items:center;gap:1rem;">🆘 SOS แจ้งปัญหา (${data.length})
            ${data.length > 1 ? `<button class="btn btn-sm btn-warning" onclick="batchResolveAllSOS()">✅ Batch Resolve All (${data.length})</button>` : ''}
            <button class="btn btn-sm btn-outline" onclick="showSOSHistory()">📋 History</button>
        </div>`;
        data.forEach(s => {
            const name = s.username ? '@' + s.username : s.first_name || 'ลูกค้า';
            const hasActive = s.has_active_sub;
            html += `<div class="pending-card">
                <div class="pending-info">
                    <span>👤 ${esc(name)} ${hasActive ? '<span style="color:var(--success);font-size:0.75rem;">● VIP</span>' : '<span style="color:var(--error);font-size:0.75rem;">● ไม่มี VIP</span>'}</span>
                    <span style="font-family:var(--font-mono);font-size:0.8rem;color:var(--text-muted);">ID: ${s.telegram_id}</span>
                    <span style="font-size:0.85rem;">💬 ${(s.message || '').slice(0, 100)}</span>
                    <span style="color:var(--text-muted);font-size:0.8rem;">🕒 ${fmtDateTime(s.created_at)}</span>
                </div>
                <div class="btn-group">
                    ${hasActive
                        ? `<button class="btn btn-sm btn-primary" id="sos-btn-${s.telegram_id}" onclick="resendSOSLinks(${s.telegram_id}, this)">🔗 ส่งลิงก์ใหม่</button>`
                        : `<button class="btn btn-sm btn-warning" onclick="sosContactCustomer(${s.telegram_id}, '${esc(name).replace(/'/g, '\\&#39;')}')">💬 แจ้งลูกค้า</button>`
                    }
                    <button class="btn btn-sm btn-outline" onclick="resolveSOSManual(${s.telegram_id})">✅ จบเคส</button>
                </div>
            </div>`;
        });
        section.innerHTML = html;
    } catch (e) {
        section.innerHTML = '';
    }
}

async function resendSOSLinks(telegramId, btn) {
    if (!confirm(`ส่งลิงก์เข้ากลุ่มใหม่ให้ลูกค้า ID: ${telegramId}?`)) return;
    btn.disabled = true;
    btn.textContent = '⏳ กำลังส่ง...';
    try {
        const result = await api(`/dashboard/sos/${telegramId}/resend-links`, { method: 'POST' });
        if (result.dm_sent) {
            btn.textContent = '✅ ส่งสำเร็จ';
            btn.className = 'btn btn-sm btn-success';
            toast(`ส่งลิงก์ใหม่สำเร็จ (${result.links_count} กลุ่ม)`, 'success');
        } else {
            btn.textContent = '⚠️ ส่ง DM ไม่ได้';
            btn.className = 'btn btn-sm btn-warning';
            toast('สร้างลิงก์สำเร็จแต่ส่ง DM ไม่ได้ (ลูกค้าอาจบล็อกบอท)', 'warning');
        }
        // Refresh SOS list after a short delay
        setTimeout(() => { loadSOSAlerts(); checkAlerts(); }, 2000);
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '🔗 ส่งลิงก์ใหม่';
        toast(e.message, 'error');
    }
}

async function sosContactCustomer(telegramId, name) {
    const defaultMsg = `สวัสดีค่ะ ${name} 🙏\n\nทางเราตรวจสอบแล้ว ยังไม่พบ VIP ที่ active อยู่ค่ะ\n\nถ้าต้องการเข้ากลุ่มใหม่ กรุณาสมัคร VIP หรือติดต่อแอดมินนะคะ 💕`;
    const msg = prompt('ข้อความถึงลูกค้า:', defaultMsg);
    if (!msg) return;
    try {
        await api(`/dashboard/sos/${telegramId}/contact`, {
            method: 'POST',
            body: JSON.stringify({ message: msg })
        });
        toast('ส่งข้อความถึงลูกค้าแล้ว ✅', 'success');
    } catch (e) { toast(e.message, 'error'); }
}

async function resolveSOSManual(telegramId) {
    if (!confirm(`จบเคส SOS ของ ID ${telegramId}? (mark as resolved)`)) return;
    try {
        await api(`/dashboard/sos/${telegramId}/resolve`, { method: 'POST' });
        toast('✅ จบเคสแล้ว', 'success');
        loadSOSAlerts();
        checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
}

async function batchResolveAllSOS() {
    if (!confirm('Resolve SOS ทั้งหมดที่ค้างอยู่?')) return;
    try {
        const result = await api('/dashboard/sos/batch-resolve', { method: 'POST' });
        toast(`✅ Resolve สำเร็จ ${result.resolved_count} รายการ`, 'success');
        loadSOSAlerts();
        checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
}

let sosHistoryPage = 1, sosHistoryFilter = 'all';
async function showSOSHistory(page) {
    if (page) sosHistoryPage = page;
    try {
        const data = await api(`/dashboard/sos-history?status=${sosHistoryFilter}&page=${sosHistoryPage}&per_page=20`);
        let html = `<div class="filters" style="margin-bottom:1rem;">
            <button class="filter-btn ${sosHistoryFilter==='all'?'active':''}" onclick="sosHistoryFilter='all';sosHistoryPage=1;showSOSHistory()">All</button>
            <button class="filter-btn ${sosHistoryFilter==='PENDING'?'active':''}" onclick="sosHistoryFilter='PENDING';sosHistoryPage=1;showSOSHistory()">Pending</button>
            <button class="filter-btn ${sosHistoryFilter==='RESOLVED'?'active':''}" onclick="sosHistoryFilter='RESOLVED';sosHistoryPage=1;showSOSHistory()">Resolved</button>
        </div>`;
        html += '<div class="table-wrap"><table><thead><tr><th>วันที่</th><th>ชื่อ</th><th>TG ID</th><th>ข้อความ</th><th>สถานะ</th><th>Resolved by</th></tr></thead><tbody>';
        data.items.forEach(s => {
            const name = s.username ? '@' + s.username : s.first_name || '-';
            html += `<tr>
                <td>${fmtDateTime(s.created_at)}</td>
                <td>${esc(name)}</td>
                <td style="font-family:var(--font-mono);font-size:0.8rem;">${s.telegram_id}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${(s.message || '-').slice(0, 80)}</td>
                <td>${statusBadge(s.status)}</td>
                <td>${esc(s.resolved_by || '-')} ${s.resolved_at ? fmtDateTime(s.resolved_at) : ''}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        html += paginationHtml(data.page, data.pages, 'showSOSHistory');
        openModal(`🆘 SOS History (${data.total} รายการ)`, html);
    } catch (e) { toast(e.message, 'error'); }
}

async function loadDashboardPendingSlips() {
    const el = document.getElementById('dashboard-pending-slips');
    if (!el) return;
    try {
        const pending = await api('/payments/pending');
        if (!pending.length) { el.innerHTML = ''; return; }
        let html = `<div class="section-title" style="margin-top:1.5rem;">🚨 สลิปรอ Approve (${pending.length})</div>`;
        pending.forEach(p => {
            const slipHtml = p.slip_file_id
                ? `<img src="/api/payments/${p.id}/slip-image" alt="สลิป" style="max-width:180px;max-height:220px;border-radius:8px;cursor:pointer;border:1px solid var(--border);" onclick="window.open(this.src)" onerror="this.outerHTML='<span style=\\'color:var(--text-dim)\\'>โหลดรูปไม่ได้</span>'">`
                : `<span style="color:var(--text-dim);font-size:0.85rem;">ไม่มีสลิป</span>`;
            html += `<div class="pending-card" style="display:flex;gap:1rem;align-items:flex-start;">
                <div style="flex-shrink:0;">${slipHtml}</div>
                <div style="flex:1;">
                    <div class="pending-info">
                        <span>👤 ${p.username ? '@'+esc(p.username) : esc(p.first_name) || p.telegram_id}</span>
                        <span style="font-weight:600;">${fmtBaht(p.amount)}</span>
                        <span style="color:var(--text-muted);font-size:0.8rem;">${fmtDateTime(p.created_at)} | ${esc(p.package_name || '')}</span>
                    </div>
                    <div class="btn-group" style="margin-top:0.5rem;">
                        <button class="btn btn-sm btn-success" onclick="approvePayment(${p.id})">✅ อนุมัติ</button>
                        <button class="btn btn-sm btn-danger" onclick="rejectPayment(${p.id})">❌ ปฏิเสธ</button>
                    </div>
                </div>
            </div>`;
        });
        el.innerHTML = html;
    } catch {}
}

// ========== PAGE: CUSTOMERS ==========
let customerSearch = '', customerFilter = 'all', customerPage = 1;
async function renderCustomers() {
    const content = document.getElementById('page-content');
    const broadcastBtn = hasRole('admin') ? `<button class="btn btn-primary" onclick="showBroadcastModal()" style="margin-bottom:1rem;">📢 Broadcast</button> <button class="btn btn-outline" onclick="showBroadcastHistory()" style="margin-bottom:1rem;">📋 Broadcast History</button>` : '';
    content.innerHTML = `
        ${broadcastBtn}
        <div class="filters">
            <input class="search-input" id="cust-search" placeholder="🔍 ค้นหา ชื่อ / Telegram ID / Username" value="${customerSearch}" onkeyup="if(event.key==='Enter'){customerSearch=this.value;customerPage=1;loadCustomers()}">
            <button class="filter-btn ${customerFilter==='all'?'active':''}" onclick="customerFilter='all';customerPage=1;loadCustomers()">All</button>
            <button class="filter-btn ${customerFilter==='active'?'active':''}" onclick="customerFilter='active';customerPage=1;loadCustomers()">Active</button>
            <button class="filter-btn ${customerFilter==='expired'?'active':''}" onclick="customerFilter='expired';customerPage=1;loadCustomers()">Expired</button>
            <button class="filter-btn ${customerFilter==='banned'?'active':''}" onclick="customerFilter='banned';customerPage=1;loadCustomers()">Banned</button>
        </div>
        <div id="customers-table"><div class="loading"><div class="spinner"></div> กำลังโหลด...</div></div>
        <div id="customers-pagination"></div>
    `;
    loadCustomers();
}

// ========== BROADCAST ==========
async function showBroadcastModal() {
    openModal('📢 Broadcast ข้อความ', `
        <div class="form-group">
            <label>กลุ่มเป้าหมาย</label>
            <select id="bc-target" onchange="updateBroadcastCount()">
                <option value="all">📋 ทุกคน</option>
                <option value="active">✅ VIP Active</option>
                <option value="expired">⏰ Expired</option>
                <option value="trial">🎯 Trial</option>
            </select>
        </div>
        <div id="bc-count-info" style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.5rem;">กำลังนับจำนวน...</div>
        <div class="form-group">
            <label>ข้อความ (รองรับ HTML)</label>
            <textarea id="bc-message" rows="6" placeholder="พิมพ์ข้อความที่จะส่ง...&#10;&#10;รองรับ HTML เช่น:&#10;<b>ตัวหนา</b>&#10;<i>ตัวเอียง</i>&#10;<a href='url'>ลิงก์</a>"></textarea>
        </div>
        <div class="form-group">
            <label>📎 แนบรูป/วิดีโอ (ไม่บังคับ, สูงสุด 20MB)</label>
            <input type="file" id="bc-media" accept="image/jpeg,image/png,image/gif,video/mp4" onchange="previewBroadcastMedia(this)" style="margin-bottom:0.5rem;">
            <div id="bc-media-preview" style="display:none;margin-bottom:0.5rem;text-align:center;"></div>
        </div>
        <div id="bc-result" style="display:none;margin-bottom:1rem;"></div>
        <button class="btn btn-primary btn-full" id="bc-send-btn" onclick="doBroadcast()">📩 ส่ง Broadcast</button>
    `);
    updateBroadcastCount();
}

function previewBroadcastMedia(input) {
    const preview = document.getElementById('bc-media-preview');
    if (!preview) return;
    preview.style.display = 'none';
    preview.innerHTML = '';
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    if (file.size > 20 * 1024 * 1024) {
        preview.style.display = 'block';
        preview.innerHTML = '<div style="color:var(--error);font-size:0.85rem;">❌ ไฟล์ใหญ่เกิน 20MB</div>';
        input.value = '';
        return;
    }
    const url = URL.createObjectURL(file);
    preview.style.display = 'block';
    if (file.type.startsWith('image/')) {
        preview.innerHTML = `<img src="${url}" style="max-width:100%;max-height:200px;border-radius:8px;">`;
    } else if (file.type.startsWith('video/')) {
        preview.innerHTML = `<video src="${url}" controls style="max-width:100%;max-height:200px;border-radius:8px;"></video>`;
    } else {
        preview.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;">📎 ${esc(file.name)}</div>`;
    }
}

async function updateBroadcastCount() {
    const target = document.getElementById('bc-target')?.value || 'all';
    const info = document.getElementById('bc-count-info');
    if (!info) return;
    info.textContent = 'กำลังนับจำนวน...';
    try {
        const data = await api(`/customers/broadcast/count?target=${target}`);
        const labels = { all: 'ทุกคน', active: 'VIP Active', expired: 'Expired', trial: 'Trial' };
        info.innerHTML = `📊 จะส่งถึง <b>${fmt(data.count)}</b> คน (${labels[target]})`;
    } catch {
        info.textContent = '❌ โหลดจำนวนไม่ได้';
    }
}

async function doBroadcast() {
    const target = document.getElementById('bc-target')?.value || 'all';
    const message = document.getElementById('bc-message')?.value?.trim();
    if (!message) { toast('กรุณาพิมพ์ข้อความ', 'error'); return; }
    
    const fileInput = document.getElementById('bc-media');
    const mediaFile = fileInput?.files?.[0] || null;
    if (mediaFile && mediaFile.size > 20 * 1024 * 1024) { toast('ไฟล์ใหญ่เกิน 20MB', 'error'); return; }
    
    const labels = { all: 'ทุกคน', active: 'VIP Active', expired: 'Expired', trial: 'Trial' };
    const mediaLabel = mediaFile ? `\n📎 แนบไฟล์: ${mediaFile.name}` : '';
    if (!confirm(`📢 ยืนยันส่ง Broadcast ไปยัง "${labels[target]}"?${mediaLabel}\n\nข้อความ:\n${message.slice(0, 200)}`)) return;
    
    const btn = document.getElementById('bc-send-btn');
    const result = document.getElementById('bc-result');
    btn.disabled = true;
    btn.textContent = '⏳ กำลังส่ง...';
    result.style.display = 'none';
    
    try {
        const fd = new FormData();
        fd.append('message', message);
        fd.append('target', target);
        fd.append('parse_mode', 'HTML');
        if (mediaFile) fd.append('media', mediaFile);
        
        const resp = await fetch('/api/customers/broadcast', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: fd,
        });
        if (resp.status === 401) { logout(); throw new Error('Session expired'); }
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: 'Error' }));
            throw new Error(err.detail || 'API Error');
        }
        const data = await resp.json();
        
        result.style.display = 'block';
        result.innerHTML = `<div class="alert-box">
            <div class="alert-box-item" style="color:var(--success);">✅ ส่งสำเร็จ: ${data.sent} คน</div>
            ${data.failed > 0 ? `<div class="alert-box-item" style="color:var(--error);">❌ ล้มเหลว: ${data.failed} คน</div>` : ''}
            <div class="alert-box-item">📊 ทั้งหมด: ${data.total} คน</div>
        </div>`;
        btn.textContent = '✅ ส่งเสร็จแล้ว';
        toast(`Broadcast สำเร็จ: ${data.sent}/${data.total}`, 'success');
    } catch (e) {
        result.style.display = 'block';
        result.innerHTML = `<div class="alert-box"><div class="alert-box-item" style="color:var(--error);">❌ ${esc(e.message)}</div></div>`;
        btn.disabled = false;
        btn.textContent = '📩 ส่ง Broadcast';
        toast(e.message, 'error');
    }
}

let bcHistoryPage = 1;
async function showBroadcastHistory(page) {
    if (page) bcHistoryPage = page;
    try {
        const data = await api(`/customers/broadcast/history?page=${bcHistoryPage}&per_page=20`);
        let html = '<div class="table-wrap"><table><thead><tr><th>วันที่</th><th>Admin</th><th>Target</th><th>ข้อความ</th><th>ส่ง/ล้มเหลว</th></tr></thead><tbody>';
        data.items.forEach(b => {
            const target = b.target_tier || b.target_group || 'all';
            const msg = (b.message_text || '').slice(0, 60) + ((b.message_text || '').length > 60 ? '...' : '');
            html += `<tr>
                <td>${fmtDateTime(b.created_at)}</td>
                <td>${esc(b.admin_name || b.admin_id)}</td>
                <td>${target}</td>
                <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.85rem;">${msg}</td>
                <td><span style="color:var(--success);">${b.total_sent}</span> / <span style="color:var(--error);">${b.total_failed}</span></td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        html += paginationHtml(data.page, data.pages, 'showBroadcastHistory');
        openModal(`📋 Broadcast History (${data.total} รายการ)`, html);
    } catch (e) { toast(e.message, 'error'); }
}

async function loadCustomers(page) {
    if (page) customerPage = page;
    customerSearch = document.getElementById('cust-search')?.value || customerSearch;
    try {
        const data = await api(`/customers?page=${customerPage}&per_page=25&search=${encodeURIComponent(customerSearch)}&status=${customerFilter}`);
        let html = '<div class="table-wrap"><table><thead><tr><th>#</th><th>ชื่อ</th><th>Telegram ID</th><th>แพ็กเกจ</th><th>สถานะ</th><th>หมดอายุ</th><th>ยอดจ่าย</th><th></th></tr></thead><tbody>';
        data.items.forEach((u, i) => {
            const status = u.sub_status || (u.is_banned ? 'BANNED' : 'NONE');
            html += `<tr>
                <td>${(customerPage-1)*25+i+1}</td>
                <td>${u.username ? '@'+esc(u.username) : esc(u.first_name) || '-'}</td>
                <td style="font-family:var(--font-mono);font-size:0.8rem;">${u.telegram_id}</td>
                <td>${esc(u.package_name || '-')}</td>
                <td>${statusBadge(status)}</td>
                <td>${fmtDate(u.end_date)}</td>
                <td>${fmtBaht(u.total_spent)}</td>
                <td><button class="btn btn-sm btn-outline" onclick="showCustomerDetail(${u.id})">📋</button></td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('customers-table').innerHTML = data.items.length ? html : '<div class="empty-state"><div class="icon">👥</div><p>ไม่พบลูกค้า</p></div>';
        document.getElementById('customers-pagination').innerHTML = paginationHtml(data.page, data.pages, 'loadCustomers');
    } catch (err) { toast(err.message, 'error'); }
}

async function showCustomerDetail(userId) {
    try {
        const [detail, payments, subs, groups] = await Promise.all([
            api(`/customers/${userId}`),
            api(`/customers/${userId}/payments`),
            api(`/customers/${userId}/subscriptions`),
            api(`/customers/${userId}/groups`),
        ]);
        const u = detail.user;
        const sub = detail.subscription;
        
        let payHtml = payments.map(p => `<tr><td>${fmtDate(p.created_at)}</td><td>${fmtBaht(p.amount)}</td><td>${esc(p.method)}</td><td>${statusBadge(p.status)}</td></tr>`).join('');
        let groupsHtml = groups.map(g => `<span class="status-badge status-active">${esc(g.slug)}</span>`).join(' ') || '-';
        
        let actionsHtml = '';
        if (hasRole('admin')) {
            actionsHtml = `
                <div class="btn-group" style="margin-top:1rem;">
                    <button class="btn btn-sm btn-success" onclick="customerAction(${userId},'extend')">✅ ต่อเวลา</button>
                    <button class="btn btn-sm btn-primary" onclick="customerAction(${userId},'upgrade')">🆙 อัพเกรด</button>
                    <button class="btn btn-sm btn-outline" onclick="customerAction(${userId},'dm')">📩 ส่ง DM</button>
                    <button class="btn btn-sm btn-warning" onclick="customerAction(${userId},'kick')">🔨 เตะ</button>
                    <button class="btn btn-sm btn-danger" onclick="customerAction(${userId},'ban')">${u.is_banned ? '🔓 ปลดแบน' : '🚫 แบน'}</button>
                </div>`;
        }
        
        openModal(`👤 ${u.username ? '@'+esc(u.username) : esc(u.first_name) || 'User'} (ID: ${u.telegram_id})`, `
            <div class="detail-panel">
                <div class="detail-row"><span class="detail-label">แพ็กเกจ</span><span class="detail-value">${sub ? sub.package_name : '-'}</span></div>
                <div class="detail-row"><span class="detail-label">สถานะ</span><span class="detail-value">${sub ? statusBadge(sub.status) : statusBadge(u.is_banned ? 'BANNED' : 'NONE')}</span></div>
                <div class="detail-row"><span class="detail-label">หมดอายุ</span><span class="detail-value">${sub ? fmtDate(sub.end_date) : '-'}</span></div>
                <div class="detail-row"><span class="detail-label">สมาชิกตั้งแต่</span><span class="detail-value">${fmtDate(u.created_at)}</span></div>
                <div class="detail-row"><span class="detail-label">ยอดจ่ายรวม</span><span class="detail-value">${fmtBaht(u.total_spent)}</span></div>
                <div class="detail-row"><span class="detail-label">กลุ่ม</span><span class="detail-value">${groupsHtml}</span></div>
            </div>
            <div class="section-title" style="margin-top:1rem;">💳 ประวัติ Payment</div>
            <div class="table-wrap"><table><thead><tr><th>วันที่</th><th>จำนวน</th><th>วิธี</th><th>สถานะ</th></tr></thead><tbody>${payHtml || '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">ไม่มี</td></tr>'}</tbody></table></div>
            ${actionsHtml}
        `);
    } catch (err) { toast(err.message, 'error'); }
}

async function customerAction(userId, action) {
    if (action === 'extend') {
        openModal('✅ ต่อเวลา', `
            <div class="form-group"><label>จำนวนวัน</label>
                <select id="extend-days"><option value="7">7 วัน</option><option value="15">15 วัน</option><option value="30" selected>30 วัน</option><option value="60">60 วัน</option><option value="90">90 วัน</option><option value="365">365 วัน</option></select>
            </div>
            <button class="btn btn-success btn-full" onclick="doExtend(${userId})">ยืนยันต่อเวลา</button>
        `);
    } else if (action === 'dm') {
        openModal('📩 ส่ง DM', `
            <div class="form-group"><label>ข้อความ</label><textarea id="dm-message" placeholder="พิมพ์ข้อความ..."></textarea></div>
            <button class="btn btn-primary btn-full" onclick="doDM(${userId})">ส่ง</button>
        `);
    } else if (action === 'ban') {
        const user = await api(`/customers/${userId}`);
        if (user.user.is_banned) {
            if (confirm('ปลดแบนผู้ใช้นี้?')) {
                await api(`/customers/${userId}/unban`, { method: 'POST' });
                toast('ปลดแบนแล้ว', 'success'); closeModal(); loadCustomers();
            }
        } else {
            openModal('🚫 แบน', `
                <div class="form-group"><label>เหตุผล</label><input id="ban-reason" placeholder="เหตุผล (ไม่บังคับ)"></div>
                <button class="btn btn-danger btn-full" onclick="doBan(${userId})">ยืนยันแบน</button>
            `);
        }
    } else if (action === 'kick') {
        const groups = await api('/groups');
        const checkboxes = groups.map(g => `<label style="display:block;margin:0.3rem 0;"><input type="checkbox" class="kick-group" value="${g.id}"> ${esc(g.slug)} — ${esc(g.title)}</label>`).join('');
        openModal('🔨 เตะออกจากกลุ่ม', `${checkboxes}<button class="btn btn-warning btn-full" style="margin-top:1rem;" onclick="doKick(${userId})">ยืนยันเตะ</button>`);
    } else if (action === 'upgrade') {
        const pkgs = await api('/settings/packages');
        const opts = pkgs.map(p => `<option value="${p.id}">${esc(p.name)} (${fmtBaht(p.price)})</option>`).join('');
        openModal('🆙 อัพเกรด', `
            <div class="form-group"><label>แพ็กเกจใหม่</label><select id="upgrade-pkg">${opts}</select></div>
            <button class="btn btn-primary btn-full" onclick="doUpgrade(${userId})">ยืนยันอัพเกรด</button>
        `);
    }
}

async function doExtend(uid) {
    if (_busy.has(`ext-${uid}`)) return;
    _busy.add(`ext-${uid}`);
    try {
        await api(`/customers/${uid}/extend`, { method: 'POST', body: JSON.stringify({ days: parseInt(document.getElementById('extend-days').value) }) });
        toast('ต่อเวลาสำเร็จ', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`ext-${uid}`); }
}
async function doDM(uid) {
    if (_busy.has(`dm-${uid}`)) return;
    _busy.add(`dm-${uid}`);
    try {
        await api(`/customers/${uid}/dm`, { method: 'POST', body: JSON.stringify({ message: document.getElementById('dm-message').value }) });
        toast('ส่ง DM แล้ว', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`dm-${uid}`); }
}
async function doBan(uid) {
    if (_busy.has(`ban-${uid}`)) return;
    _busy.add(`ban-${uid}`);
    try {
        await api(`/customers/${uid}/ban`, { method: 'POST', body: JSON.stringify({ reason: document.getElementById('ban-reason').value }) });
        toast('แบนแล้ว', 'success'); closeModal(); loadCustomers();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`ban-${uid}`); }
}
async function doKick(uid) {
    if (_busy.has(`kick-${uid}`)) return;
    const ids = [...document.querySelectorAll('.kick-group:checked')].map(c => parseInt(c.value));
    if (!ids.length) { toast('เลือกกลุ่มก่อน', 'error'); return; }
    _busy.add(`kick-${uid}`);
    try {
        await api(`/customers/${uid}/kick`, { method: 'POST', body: JSON.stringify({ group_ids: ids }) });
        toast('เตะแล้ว', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`kick-${uid}`); }
}
async function doUpgrade(uid) {
    if (_busy.has(`upg-${uid}`)) return;
    _busy.add(`upg-${uid}`);
    try {
        await api(`/customers/${uid}/upgrade`, { method: 'POST', body: JSON.stringify({ package_id: parseInt(document.getElementById('upgrade-pkg').value) }) });
        toast('อัพเกรดสำเร็จ', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`upg-${uid}`); }
}

// ========== PAGE: FINANCE ==========
let financeFilter = 'all', financePage = 1;
async function renderFinance() {
    const content = document.getElementById('page-content');
    
    let summaryHtml = '';
    if (hasRole('admin')) {
        try {
            const s = await api('/payments/summary');
            summaryHtml = `<div class="cards-grid">
                <div class="card"><div class="card-label">วันนี้</div><div class="card-value">${fmtBaht(s.today)}</div></div>
                <div class="card"><div class="card-label">สัปดาห์</div><div class="card-value">${fmtBaht(s.week)}</div></div>
                <div class="card"><div class="card-label">เดือน</div><div class="card-value">${fmtBaht(s.month)}</div></div>
                <div class="card"><div class="card-label">ปี</div><div class="card-value">${fmtBaht(s.year)}</div></div>
            </div>`;
        } catch {}
    }
    
    content.innerHTML = `${summaryHtml}
        <div id="pending-slips"></div>
        <div id="expired-pending"></div>
        <div class="filters">
            <button class="filter-btn ${financeFilter==='all'?'active':''}" onclick="financeFilter='all';financePage=1;loadPayments()">All</button>
            <button class="filter-btn ${financeFilter==='PENDING'?'active':''}" onclick="financeFilter='PENDING';financePage=1;loadPayments()">Pending</button>
            <button class="filter-btn ${financeFilter==='CONFIRMED'?'active':''}" onclick="financeFilter='CONFIRMED';financePage=1;loadPayments()">Confirmed</button>
            <button class="filter-btn ${financeFilter==='REJECTED'?'active':''}" onclick="financeFilter='REJECTED';financePage=1;loadPayments()">Rejected</button>
        </div>
        <div id="payments-table"></div>
        <div id="payments-pagination"></div>
        ${hasRole('admin') ? '<div class="cards-grid" style="margin-top:1.5rem;"><div class="card card-wide"><div class="card-label">📊 รายได้ตามแพ็กเกจ</div><div class="chart-container"><canvas id="pkg-chart"></canvas></div></div><div class="card card-wide"><div class="card-label">📊 รายได้ตามวิธีชำระ</div><div class="chart-container"><canvas id="method-chart"></canvas></div></div></div>' : ''}
    `;
    
    loadPendingSlips();
    loadExpiredPending();
    loadPayments();
    if (hasRole('admin')) loadFinanceCharts();
}

async function loadPendingSlips() {
    try {
        const pending = await api('/payments/pending');
        if (!pending.length) { document.getElementById('pending-slips').innerHTML = ''; return; }
        let html = `<div class="section-title">🚨 สลิปรอ Approve (${pending.length})</div>`;
        pending.forEach(p => {
            const slipHtml = p.slip_file_id
                ? `<img src="/api/payments/${p.id}/slip-image" alt="สลิป" style="max-width:180px;max-height:220px;border-radius:8px;cursor:pointer;border:1px solid var(--border);" onclick="window.open(this.src)" onerror="this.outerHTML='<span style=\\'color:var(--text-dim)\\'>โหลดรูปไม่ได้</span>'">`
                : `<span style="color:var(--text-dim);font-size:0.85rem;">ไม่มีสลิป</span>`;
            html += `<div class="pending-card" style="display:flex;gap:1rem;align-items:flex-start;">
                <div style="flex-shrink:0;">${slipHtml}</div>
                <div style="flex:1;">
                    <div class="pending-info">
                        <span>👤 ${p.username ? '@'+esc(p.username) : esc(p.first_name) || p.telegram_id}</span>
                        <span style="font-weight:600;">${fmtBaht(p.amount)}</span>
                        <span style="color:var(--text-muted);font-size:0.8rem;">${fmtDateTime(p.created_at)} | ${esc(p.package_name || '')}</span>
                    </div>
                    <div class="btn-group" style="margin-top:0.5rem;">
                        <button class="btn btn-sm btn-success" onclick="approvePayment(${p.id})">✅ อนุมัติ</button>
                        <button class="btn btn-sm btn-danger" onclick="rejectPayment(${p.id})">❌ ปฏิเสธ</button>
                    </div>
                </div>
            </div>`;
        });
        document.getElementById('pending-slips').innerHTML = html;
    } catch {}
}

async function loadExpiredPending() {
    try {
        const expired = await api('/payments/pending-expired');
        const el = document.getElementById('expired-pending');
        if (!expired.length) { el.innerHTML = ''; return; }
        let html = `<details style="margin-bottom:1rem;"><summary style="cursor:pointer;color:var(--text-muted);font-size:0.9rem;">⏰ PENDING หมดอายุ (${expired.length} รายการ เก่ากว่า 24 ชม.)</summary>`;
        html += '<div style="margin-top:0.5rem;">';
        expired.forEach(p => {
            html += `<div class="pending-card" style="border-color:var(--text-dim);opacity:0.7;">
                <div class="pending-info">
                    <span>👤 ${p.username ? '@'+esc(p.username) : esc(p.first_name) || p.telegram_id}</span>
                    <span style="font-weight:600;">${fmtBaht(p.amount)}</span>
                    <span style="color:var(--text-dim);font-size:0.8rem;">${fmtDateTime(p.created_at)}</span>
                </div>
                <div class="btn-group">
                    <button class="btn btn-sm btn-success" onclick="approvePayment(${p.id})">✅</button>
                    <button class="btn btn-sm btn-danger" onclick="rejectPayment(${p.id})">❌</button>
                </div>
            </div>`;
        });
        html += '</div></details>';
        el.innerHTML = html;
    } catch {}
}

async function approvePayment(id) {
    if (_busy.has(`appr-${id}`)) return;
    if (!confirm('อนุมัติสลิปนี้?')) return;
    _busy.add(`appr-${id}`);
    try {
        await api(`/payments/${id}/approve`, { method: 'POST' });
        toast('อนุมัติแล้ว', 'success');
        loadPendingSlips(); loadDashboardPendingSlips(); loadPayments(); checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`appr-${id}`); }
}

async function rejectPayment(id) {
    if (_busy.has(`rej-${id}`)) return;
    const reason = prompt('เหตุผลปฏิเสธ:');
    if (reason === null) return;
    _busy.add(`rej-${id}`);
    try {
        await api(`/payments/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) });
        toast('ปฏิเสธแล้ว', 'success');
        loadPendingSlips(); loadDashboardPendingSlips(); loadPayments(); checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`rej-${id}`); }
}

async function loadPayments(page) {
    if (page) financePage = page;
    try {
        const data = await api(`/payments?page=${financePage}&per_page=25&status=${financeFilter}`);
        let html = '<div class="table-wrap"><table><thead><tr><th>วันที่</th><th>ชื่อ</th><th>จำนวน</th><th>วิธี</th><th>แพ็กเกจ</th><th>สถานะ</th></tr></thead><tbody>';
        data.items.forEach(p => {
            html += `<tr>
                <td>${fmtDateTime(p.created_at)}</td>
                <td>${p.username ? '@'+esc(p.username) : esc(p.first_name) || '-'}</td>
                <td style="font-weight:600;">${fmtBaht(p.amount)}</td>
                <td>${esc(p.method)}</td>
                <td>${esc(p.package_name)}</td>
                <td>${statusBadge(p.status)}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('payments-table').innerHTML = data.items.length ? html : '<div class="empty-state">ไม่มีรายการ</div>';
        document.getElementById('payments-pagination').innerHTML = paginationHtml(data.page, data.pages, 'loadPayments');
    } catch (e) { toast(e.message, 'error'); }
}

async function loadFinanceCharts() {
    try {
        const [byPkg, byMethod] = await Promise.all([api('/payments/chart/by-package'), api('/payments/chart/by-method')]);
        const colors = ['#00d4ff', '#00d2d3', '#feca57', '#ff6b6b', '#a29bfe', '#fd79a8'];
        
        if (byPkg.length) {
            charts.pkg = new Chart(document.getElementById('pkg-chart'), {
                type: 'doughnut',
                data: { labels: byPkg.map(r => r.name), datasets: [{ data: byPkg.map(r => parseFloat(r.total)), backgroundColor: colors }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#e0e6f0' } } } }
            });
        }
        if (byMethod.length) {
            charts.method = new Chart(document.getElementById('method-chart'), {
                type: 'doughnut',
                data: { labels: byMethod.map(r => r.method), datasets: [{ data: byMethod.map(r => parseFloat(r.total)), backgroundColor: colors }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#e0e6f0' } } } }
            });
        }
    } catch {}
}

// ========== PAGE: PROMOTIONS ==========
let promoTab = 'campaigns';
async function renderPromotions() {
    const content = document.getElementById('page-content');
    let statsHtml = '';
    try {
        const ps = await api('/promo-stats');
        statsHtml = `<div class="cards-grid" style="margin-bottom:1rem;">
            <div class="card"><div class="card-label">🎟 Code ใช้แล้ว</div><div class="card-value">${fmt(ps.codes_used)}</div></div>
            <div class="card"><div class="card-label">💸 ส่วนลดรวม</div><div class="card-value">${fmtBaht(ps.total_discount)}</div></div>
            <div class="card"><div class="card-label">⚡ Flash Sale ขายไป</div><div class="card-value">${fmt(ps.flash_sold)} slots</div></div>
            <div class="card"><div class="card-label">💰 Flash Revenue</div><div class="card-value">${fmtBaht(ps.flash_revenue)}</div></div>
        </div>`;
    } catch {}
    content.innerHTML = `${statsHtml}
        <div class="tabs">
            <div class="tab ${promoTab==='campaigns'?'active':''}" onclick="promoTab='campaigns';renderPromotions()">🎯 Campaign Center</div>
            <div class="tab ${promoTab==='performance'?'active':''}" onclick="promoTab='performance';renderPromotions()">📊 ผลลัพธ์โปร</div>
            <div class="tab ${promoTab==='flash'?'active':''}" onclick="promoTab='flash';renderPromotions()">⚡ Flash Sale</div>
            <div class="tab ${promoTab==='code'?'active':''}" onclick="promoTab='code';renderPromotions()">🎟 Promo Code</div>
            <div class="tab ${promoTab==='scheduled'?'active':''}" onclick="promoTab='scheduled';renderPromotions()">📅 ตั้งเวลาโปรโมท</div>
        </div>
        <div id="promo-content"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (promoTab === 'campaigns') loadPromotionCampaigns();
    else if (promoTab === 'performance') loadPromoPerformance();
    else if (promoTab === 'flash') loadFlashSales();
    else if (promoTab === 'code') loadPromoCodes();
    else loadScheduledPromos();
}


async function loadPromotionCampaigns() {
    try {
        const data = await api('/promotion-campaigns');
        const rows = data.length ? data.map(c => `<tr>
            <td><div style="font-weight:600;">${esc(c.name)}</div><div style="color:var(--text-muted);font-size:0.8rem;">${esc(c.package_name || '')}</div></td>
            <td>${fmtBaht(c.normal_price)} → <b style="color:var(--primary);">${fmtBaht(c.promo_price)}</b></td>
            <td>${fmtDateTime(c.starts_at)}<br><span style="color:var(--text-muted);">ถึง ${fmtDateTime(c.ends_at)}</span><br><span style="color:var(--primary);font-size:0.75rem;">${(c.delivery_channels || []).join(", ")}</span></td>
            <td>${c.is_active ? '<span style="color:var(--success)">🟢 Active</span>' : '<span style="color:var(--text-dim)">⭕ ปิด</span>'}</td>
            <td>${fmt(c.buyers)} คน / ${fmt(c.orders)} ออเดอร์</td>
            <td style="font-weight:600;color:var(--success);">${fmtBaht(c.revenue)}</td>
            <td><div class="btn-group">
                <button class="btn btn-sm btn-outline" onclick="togglePromotionCampaign(${c.id})">${c.is_active ? '⏸ ปิด' : '▶ เปิด'}</button>
                <button class="btn btn-sm btn-danger" onclick="deletePromotionCampaign(${c.id})">🗑</button>
            </div></td>
        </tr>`).join('') : `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);">ยังไม่มีแคมเปญโปร</td></tr>`;

        document.getElementById('promo-content').innerHTML = `
            <div class="alert-box" style="margin-bottom:1rem;">
                <b>Promotion Campaign Center</b><br>
                สร้างโปรแบบครบชุด: ราคาโปร + คำขายบอท + caption ส่งกลุ่ม/ลูกค้า + วัดยอดขายอัตโนมัติจาก payment ที่ตรงแพ็กเกจ/ราคา/ช่วงเวลา
            </div>
            <button class="btn btn-primary" onclick="showPromotionCampaignForm()" style="margin-bottom:1rem;">+ สร้างแคมเปญโปรใหม่</button>
            <div class="table-wrap"><table><thead><tr><th>แคมเปญ</th><th>ราคา</th><th>ช่วงเวลา</th><th>สถานะ</th><th>ยอดซื้อ</th><th>รายได้</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
        `;
    } catch (e) { toast(e.message, 'error'); }
}

async function showPromotionCampaignForm() {
    const packages = await api('/settings/packages');
    const pkgOptions = packages.map(p => `<option value="${p.id}" data-price="${p.price}">${esc(p.name)} — ${fmtBaht(p.price)}</option>`).join('');
    openModal('🎯 สร้างแคมเปญโปร', `
        <div class="form-group"><label>ชื่อโปร</label><input id="camp-name" placeholder="เช่น โปรสิ้นเดือน VIP 300 เหลือ 200"></div>
        <div class="form-row">
            <div class="form-group"><label>แพ็กเกจ</label><select id="camp-pkg" onchange="syncCampaignPackagePrice()">${pkgOptions}</select><div class="dm-description">เลือกชื่อแพ็กเกจได้เลย ไม่ต้องจำเลข ID</div></div>
            <div class="form-group"><label>ราคาเดิม</label><input id="camp-normal" type="number" value="300"></div>
            <div class="form-group"><label>ราคาโปร</label><input id="camp-promo" type="number" value="200"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>เริ่ม</label><input id="camp-start" type="datetime-local"></div>
            <div class="form-group"><label>สิ้นสุด</label><input id="camp-end" type="datetime-local"></div>
        </div>
        <div class="form-group"><label>Badge/คำสั้นหน้าแพ็กเกจบอท</label><input id="camp-badge" placeholder="🔥 โปร 300 เหลือ 200 ถึงคืนนี้"></div>
        <div class="form-group"><label>คำขายในบอทตอนลูกค้ากดซื้อ</label><textarea id="camp-bot-text" placeholder="ข้อความอธิบายโปรใน Sales Bot"></textarea></div>
        <div class="form-group"><label>Caption ส่งเข้ากลุ่ม</label><textarea id="camp-group-caption" placeholder="ข้อความโปรสำหรับกลุ่มฟรี"></textarea></div>
        <div class="form-group"><label>Caption Broadcast ลูกค้า</label><textarea id="camp-user-caption" placeholder="ข้อความโปรสำหรับยิงหาลูกค้า"></textarea></div>
        <div class="form-group"><label>ช่องทางที่จะใช้โปรนี้</label>
            <label style="display:block;color:var(--text);"><input type="checkbox" class="camp-channel" value="bot_package" style="width:auto;margin-right:0.5rem;"> แสดงราคา/คำขายใน Sales Bot</label>
            <label style="display:block;color:var(--text);"><input type="checkbox" class="camp-channel" value="group_post" style="width:auto;margin-right:0.5rem;"> ส่งโปรเข้ากลุ่ม Telegram</label>
            <label style="display:block;color:var(--text);"><input type="checkbox" class="camp-channel" value="user_broadcast" style="width:auto;margin-right:0.5rem;"> Broadcast หาลูกค้า</label>
            <label style="display:block;color:var(--text);"><input type="checkbox" class="camp-channel" value="tracking_only" checked style="width:auto;margin-right:0.5rem;"> Tracking only / ยังไม่ส่งออก</label>
            <div class="dm-description">ตอนนี้การเลือกนี้คือบันทึกแผน/กันพลาด ยังไม่ยิงข้อความทันทีจนกว่าจะมีปุ่ม Run/ตั้งเวลาเฉพาะช่องทาง</div>
        </div>
        <div class="form-group"><label>Target Groups (คั่นด้วย comma)</label><input id="camp-targets" placeholder="FREE1,FREE2 หรือ chat_id"></div>
        <div class="form-group"><label>รูปโปร</label><input id="camp-image-file" type="file" accept="image/png,image/jpeg,image/webp" onchange="uploadCampaignImage()"><input id="camp-image" placeholder="URL รูปจะถูกใส่อัตโนมัติหลังอัปโหลด" style="margin-top:0.5rem;"><div id="camp-image-preview" class="dm-description">รองรับ JPG / PNG / WEBP ไม่เกิน 8MB</div></div>
        <button class="btn btn-primary btn-full" onclick="createPromotionCampaign()">💾 สร้างแคมเปญ</button>
    `);
    syncCampaignPackagePrice();
}

function syncCampaignPackagePrice() {
    const sel = document.getElementById('camp-pkg');
    const normal = document.getElementById('camp-normal');
    if (sel && normal) normal.value = parseFloat(sel.selectedOptions[0]?.dataset.price || normal.value || 0);
}


async function uploadCampaignImage() {
    const input = document.getElementById('camp-image-file');
    const file = input?.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const preview = document.getElementById('camp-image-preview');
    if (preview) preview.textContent = 'กำลังอัปโหลดรูป...';
    try {
        const resp = await fetch('/api/promotion-campaigns/upload-image', {
            method: 'POST',
            headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            body: fd,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: 'Upload failed' }));
            throw new Error(err.detail || 'Upload failed');
        }
        const data = await resp.json();
        document.getElementById('camp-image').value = data.url;
        if (preview) preview.innerHTML = `อัปโหลดแล้ว: <a href="${data.url}" target="_blank" style="color:var(--primary);">เปิดดูรูป</a><br><img src="${data.url}" style="margin-top:0.5rem;max-width:220px;max-height:160px;border-radius:10px;border:1px solid var(--border);">`;
        toast('อัปโหลดรูปโปรแล้ว', 'success');
    } catch (e) {
        if (preview) preview.textContent = e.message;
        toast(e.message, 'error');
    }
}

async function createPromotionCampaign() {
    try {
        const targets = (document.getElementById('camp-targets').value || '').split(',').map(x => x.trim()).filter(Boolean);
        const channels = Array.from(document.querySelectorAll('.camp-channel:checked')).map(x => x.value);
        await api('/promotion-campaigns', { method: 'POST', body: JSON.stringify({
            name: document.getElementById('camp-name').value,
            package_id: parseInt(document.getElementById('camp-pkg').value),
            normal_price: parseFloat(document.getElementById('camp-normal').value),
            promo_price: parseFloat(document.getElementById('camp-promo').value),
            starts_at: document.getElementById('camp-start').value,
            ends_at: document.getElementById('camp-end').value,
            bot_badge: document.getElementById('camp-badge').value,
            bot_sales_text: document.getElementById('camp-bot-text').value,
            group_caption: document.getElementById('camp-group-caption').value,
            user_broadcast_caption: document.getElementById('camp-user-caption').value,
            target_groups: targets,
            delivery_channels: channels.length ? channels : ['tracking_only'],
            image_path: document.getElementById('camp-image').value,
        })});
        toast('สร้างแคมเปญโปรแล้ว', 'success'); closeModal(); loadPromotionCampaigns();
    } catch (e) { toast(e.message, 'error'); }
}

async function togglePromotionCampaign(id) {
    await api(`/promotion-campaigns/${id}/toggle`, { method: 'POST' });
    loadPromotionCampaigns();
}
async function deletePromotionCampaign(id) {
    if (!confirm('ลบแคมเปญนี้?')) return;
    await api(`/promotion-campaigns/${id}`, { method: 'DELETE' });
    loadPromotionCampaigns();
}

async function loadPromoPerformance() {
    try {
        const data = await api('/promo-performance');
        const flashRows = data.flash_sales.length ? data.flash_sales.map(f => `<tr>
            <td>${esc(f.name)}</td><td>${esc(f.package_name || '-')}</td><td>${fmtBaht(f.flash_price)}</td>
            <td>${fmt(f.sold_slots)}/${fmt(f.total_slots)}</td><td style="font-weight:600;color:var(--success);">${fmtBaht(f.revenue)}</td>
            <td>${fmtBaht(f.discount_saved)}</td><td>${fmtDateTime(f.starts_at)} - ${fmtDateTime(f.ends_at)}</td>
        </tr>`).join('') : `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);">ยังไม่มี Flash Sale</td></tr>`;

        const codeRows = data.promo_codes.length ? data.promo_codes.map(c => `<tr>
            <td style="font-family:var(--font-mono);color:var(--primary);">${esc(c.code)}</td><td>${c.discount_pct}%</td>
            <td>${fmt(c.buyers)} คน</td><td>${fmt(c.tracked_uses || c.used_count)}/${fmt(c.max_uses)}</td>
            <td style="font-weight:600;color:var(--success);">${fmtBaht(c.revenue)}</td><td>${fmtBaht(c.discount_total)}</td><td>${fmtDate(c.expires_at)}</td>
        </tr>`).join('') : `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);">ยังไม่มี Promo Code ที่ถูกใช้</td></tr>`;

        const schedRows = data.scheduled_promotions.length ? data.scheduled_promotions.map(s => `<tr>
            <td>${esc(s.name)}</td><td>${fmtDateTime(s.scheduled_at)}</td><td>${esc(s.repeat_type)}</td>
            <td>${s.is_sent ? '<span style="color:var(--success)">ส่งแล้ว</span>' : s.is_active ? '<span style="color:var(--warning)">รอส่ง</span>' : 'ปิด'}</td>
            <td style="color:var(--text-muted);">ยังไม่ผูกยอดขายอัตโนมัติ</td>
        </tr>`).join('') : `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);">ยังไม่มีโปรตั้งเวลา</td></tr>`;

        document.getElementById('promo-content').innerHTML = `
            <div class="cards-grid" style="margin-bottom:1rem;">
                <div class="card metric-card primary"><div class="card-label">Flash Sale ขายได้</div><div class="card-value">${fmt(data.summary.flash_sold)} slot</div></div>
                <div class="card metric-card primary"><div class="card-label">รายได้ Flash Sale</div><div class="card-value">${fmtBaht(data.summary.flash_revenue)}</div></div>
                <div class="card metric-card success"><div class="card-label">Promo Code ซื้อ</div><div class="card-value">${fmt(data.summary.promo_code_buyers)} คน</div></div>
                <div class="card metric-card success"><div class="card-label">รายได้ Promo Code</div><div class="card-value">${fmtBaht(data.summary.promo_code_revenue)}</div></div>
            </div>
            <div class="section-title">⚡ Flash Sale Performance</div>
            <div class="table-wrap"><table><thead><tr><th>ชื่อโปร</th><th>แพ็กเกจ</th><th>ราคาโปร</th><th>ขายได้</th><th>รายได้</th><th>ส่วนลดรวม</th><th>ช่วงเวลา</th></tr></thead><tbody>${flashRows}</tbody></table></div>
            <div class="section-title">🎟 Promo Code Performance</div>
            <div class="table-wrap"><table><thead><tr><th>Code</th><th>ลด</th><th>คนซื้อ</th><th>ใช้แล้ว</th><th>รายได้</th><th>ส่วนลดรวม</th><th>หมดอายุ</th></tr></thead><tbody>${codeRows}</tbody></table></div>
            <div class="section-title">📅 โปรโมทตั้งเวลา</div>
            <div class="alert-box" style="color:var(--text-muted);">หมายเหตุ: โปรแบบ “ตั้งเวลาโพสต์/ส่งข้อความ” ตอนนี้วัดได้แค่ว่าส่งแล้วหรือยัง ยังไม่ได้ผูกยอดขายกลับมาที่แคมเปญแบบอัตโนมัติ</div>
            <div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>เวลาส่ง</th><th>รอบ</th><th>สถานะ</th><th>การนับยอด</th></tr></thead><tbody>${schedRows}</tbody></table></div>
        `;
    } catch (e) { toast(e.message, 'error'); }
}

async function loadFlashSales() {
    try {
        const data = await api('/flash-sales');
        let html = `<button class="btn btn-primary" onclick="showFlashSaleForm()" style="margin-bottom:1rem;">+ สร้าง Flash Sale ใหม่</button>`;
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>ราคา</th><th>Slot</th><th>Sold</th><th>เริ่ม</th><th>สิ้นสุด</th><th>สถานะ</th><th></th></tr></thead><tbody>';
        data.forEach(s => {
            html += `<tr>
                <td>${esc(s.name)}</td><td>${fmtBaht(s.flash_price)}</td><td>${s.total_slots}</td><td>${s.sold_slots}</td>
                <td>${fmtDateTime(s.starts_at)}</td><td>${fmtDateTime(s.ends_at)}</td>
                <td>${s.is_active ? '<span style="color:var(--success)">🟢 Live</span>' : '<span style="color:var(--text-dim)">⭕ ปิด</span>'}</td>
                <td><div class="btn-group">
                    <button class="btn btn-sm btn-outline" onclick="toggleFlashSale(${s.id})">${s.is_active ? '⏸' : '▶'}</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteFlashSale(${s.id})">🗑</button>
                </div></td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('promo-content').innerHTML = data.length ? html : `<button class="btn btn-primary" onclick="showFlashSaleForm()">+ สร้าง Flash Sale ใหม่</button><div class="empty-state" style="margin-top:1rem;"><div class="icon">⚡</div><p>ยังไม่มี Flash Sale</p></div>`;
    } catch (e) { toast(e.message, 'error'); }
}

async function showFlashSaleForm() {
    const packages = await api('/settings/packages');
    const pkgOptions = packages.map(p => `<option value="${p.id}" data-price="${p.price}">${esc(p.name)} — ${fmtBaht(p.price)}</option>`).join('');
    openModal('⚡ สร้าง Flash Sale', `
        <div class="form-group"><label>ชื่อ</label><input id="fs-name" placeholder="ชื่อ Flash Sale"></div>
        <div class="form-row">
            <div class="form-group"><label>ราคา Flash</label><input id="fs-price" type="number" placeholder="199"></div>
            <div class="form-group"><label>ราคาเดิม</label><input id="fs-orig" type="number" placeholder="300"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>แพ็กเกจ</label><select id="fs-pkg" onchange="syncFlashPackagePrice()">${pkgOptions}</select><div class="dm-description">เลือกชื่อแพ็กเกจ</div></div>
            <div class="form-group"><label>จำนวน Slot</label><input id="fs-slots" type="number" value="30"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>เริ่ม</label><input id="fs-start" type="datetime-local"></div>
            <div class="form-group"><label>สิ้นสุด</label><input id="fs-end" type="datetime-local"></div>
        </div>
        <button class="btn btn-primary btn-full" onclick="createFlashSale()">💾 สร้าง</button>
    `);
    syncFlashPackagePrice();
}

function syncFlashPackagePrice() {
    const sel = document.getElementById('fs-pkg');
    const orig = document.getElementById('fs-orig');
    if (sel && orig) orig.value = parseFloat(sel.selectedOptions[0]?.dataset.price || orig.value || 0);
}

async function createFlashSale() {
    try {
        await api('/flash-sales', { method: 'POST', body: JSON.stringify({
            name: document.getElementById('fs-name').value,
            flash_price: parseFloat(document.getElementById('fs-price').value),
            original_price: parseFloat(document.getElementById('fs-orig').value),
            package_id: parseInt(document.getElementById('fs-pkg').value),
            total_slots: parseInt(document.getElementById('fs-slots').value),
            starts_at: document.getElementById('fs-start').value,
            ends_at: document.getElementById('fs-end').value,
        })});
        toast('สร้าง Flash Sale สำเร็จ', 'success'); closeModal(); loadFlashSales();
    } catch (e) { toast(e.message, 'error'); }
}

async function toggleFlashSale(id) {
    await api(`/flash-sales/${id}/toggle`, { method: 'POST' }); loadFlashSales();
}
async function deleteFlashSale(id) {
    if (!confirm('ลบ Flash Sale นี้?')) return;
    await api(`/flash-sales/${id}`, { method: 'DELETE' }); loadFlashSales();
}

async function loadPromoCodes() {
    try {
        const data = await api('/promo-codes');
        let html = `<button class="btn btn-primary" onclick="showPromoCodeForm()" style="margin-bottom:1rem;">+ สร้าง Promo Code</button>`;
        html += '<div class="table-wrap"><table><thead><tr><th>Code</th><th>ส่วนลด</th><th>ใช้แล้ว/ทั้งหมด</th><th>หมดอายุ</th><th>สถานะ</th><th></th></tr></thead><tbody>';
        data.forEach(c => {
            html += `<tr>
                <td style="font-family:var(--font-mono);color:var(--primary);">${esc(c.code)}</td>
                <td>${c.discount_pct}%</td><td>${c.used_count}/${c.max_uses}</td>
                <td>${fmtDate(c.expires_at)}</td>
                <td>${c.is_active ? '<span style="color:var(--success)">Active</span>' : '<span style="color:var(--text-dim)">Off</span>'}</td>
                <td><div class="btn-group">
                    <button class="btn btn-sm btn-outline" onclick="togglePromoCode(${c.id})">${c.is_active?'⏸':'▶'}</button>
                    <button class="btn btn-sm btn-danger" onclick="deletePromoCode(${c.id})">🗑</button>
                </div></td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('promo-content').innerHTML = data.length ? html : `<button class="btn btn-primary" onclick="showPromoCodeForm()">+ สร้าง Promo Code</button><div class="empty-state" style="margin-top:1rem;"><div class="icon">🎟</div><p>ยังไม่มี Promo Code</p></div>`;
    } catch (e) { toast(e.message, 'error'); }
}

function showPromoCodeForm() {
    openModal('🎟 สร้าง Promo Code', `
        <div class="form-row">
            <div class="form-group"><label>Code</label><input id="pc-code" placeholder="SAVE20" style="text-transform:uppercase;"></div>
            <div class="form-group"><label>&nbsp;</label><button class="btn btn-outline btn-full" onclick="document.getElementById('pc-code').value='PROMO'+Math.random().toString(36).substr(2,5).toUpperCase()">🎲 สุ่ม</button></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>ส่วนลด %</label><input id="pc-pct" type="number" value="10"></div>
            <div class="form-group"><label>ใช้ได้ (ครั้ง)</label><input id="pc-uses" type="number" value="50"></div>
        </div>
        <div class="form-group"><label>หมดอายุ</label><input id="pc-exp" type="datetime-local"></div>
        <button class="btn btn-primary btn-full" onclick="createPromoCode()">💾 สร้าง</button>
    `);
}

async function createPromoCode() {
    try {
        await api('/promo-codes', { method: 'POST', body: JSON.stringify({
            code: document.getElementById('pc-code').value,
            discount_pct: parseInt(document.getElementById('pc-pct').value),
            max_uses: parseInt(document.getElementById('pc-uses').value),
            expires_at: document.getElementById('pc-exp').value,
        })});
        toast('สร้าง Promo Code สำเร็จ', 'success'); closeModal(); loadPromoCodes();
    } catch (e) { toast(e.message, 'error'); }
}

async function togglePromoCode(id) { await api(`/promo-codes/${id}/toggle`, { method: 'POST' }); loadPromoCodes(); }
async function deletePromoCode(id) { if (!confirm('ลบ?')) return; await api(`/promo-codes/${id}`, { method: 'DELETE' }); loadPromoCodes(); }

async function loadScheduledPromos() {
    try {
        const data = await api('/scheduled-promotions');
        let html = `<button class="btn btn-primary" onclick="showScheduledForm()" style="margin-bottom:1rem;">+ สร้างโปรโมทตั้งเวลา</button>`;
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>เวลา</th><th>ทุก</th><th>สถานะ</th><th></th></tr></thead><tbody>';
        data.forEach(s => {
            html += `<tr><td>${esc(s.name)}</td><td>${fmtDateTime(s.scheduled_at)}</td><td>${esc(s.repeat_type)}</td>
                <td>${s.is_sent ? '<span style="color:var(--success)">✅ ส่งแล้ว</span>' : s.is_active ? '<span style="color:var(--warning)">⏳ รอ</span>' : 'Off'}</td>
                <td><button class="btn btn-sm btn-danger" onclick="deleteScheduledPromo(${s.id})">🗑</button></td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('promo-content').innerHTML = data.length ? html : `<button class="btn btn-primary" onclick="showScheduledForm()">+ สร้าง</button><div class="empty-state" style="margin-top:1rem;"><div class="icon">📅</div><p>ไม่มีโปรโมทตั้งเวลา</p></div>`;
    } catch (e) { toast(e.message, 'error'); }
}

function showScheduledForm() {
    openModal('📅 สร้างโปรโมทตั้งเวลา', `
        <div class="form-group"><label>ชื่อ</label><input id="sp-name" placeholder="ชื่อโปรโมชั่น"></div>
        <div class="form-group"><label>ข้อความ</label><textarea id="sp-msg" placeholder="ข้อความที่จะส่ง..."></textarea></div>
        <div class="form-row">
            <div class="form-group"><label>เวลา</label><input id="sp-time" type="datetime-local"></div>
            <div class="form-group"><label>ทุก</label><select id="sp-repeat"><option value="once">ครั้งเดียว</option><option value="daily">ทุกวัน</option><option value="weekly">ทุกสัปดาห์</option></select></div>
        </div>
        <button class="btn btn-primary btn-full" onclick="createScheduledPromo()">💾 บันทึก</button>
    `);
}

async function createScheduledPromo() {
    try {
        await api('/scheduled-promotions', { method: 'POST', body: JSON.stringify({
            name: document.getElementById('sp-name').value,
            message_text: document.getElementById('sp-msg').value,
            scheduled_at: document.getElementById('sp-time').value,
            repeat_type: document.getElementById('sp-repeat').value,
            target_groups: ["G300"],
        })});
        toast('สร้างแล้ว', 'success'); closeModal(); loadScheduledPromos();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteScheduledPromo(id) {
    if (!confirm('ลบ?')) return;
    await api(`/scheduled-promotions/${id}`, { method: 'DELETE' }); loadScheduledPromos();
}

// ========== PAGE: CONTENT ==========
let contentTab = 'queue';
async function renderContent() {
    const content = document.getElementById('page-content');
    content.innerHTML = `
        <div class="tabs">
            <div class="tab ${contentTab==='queue'?'active':''}" onclick="contentTab='queue';renderContent()">📦 Queue</div>
            <div class="tab ${contentTab==='schedule'?'active':''}" onclick="contentTab='schedule';renderContent()">📅 Schedule</div>
            <div class="tab ${contentTab==='stats'?'active':''}" onclick="contentTab='stats';renderContent()">📊 สถิติ</div>
        </div>
        <div id="content-area"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (contentTab === 'queue') loadContentQueue();
    else if (contentTab === 'schedule') loadContentSchedule();
    else loadContentStats();
}

async function loadContentQueue() {
    try {
        const data = await api('/content/queue');
        let html = '';
        
        // Upload zone (admin+)
        if (hasRole('admin')) {
            html += `
            <div class="upload-zone" id="upload-zone" onclick="document.getElementById('upload-input').click()">
                <div class="icon">📸</div>
                <p>คลิกเพื่ออัพโหลดรูป หรือลากไฟล์มาวางที่นี่</p>
                <div class="hint">รองรับ: JPG, PNG, GIF, WEBP, MP4 (สูงสุด 20MB)</div>
                <input type="file" id="upload-input" accept="image/*,video/mp4" multiple style="display:none" onchange="handleContentUpload(this.files)">
            </div>
            <div id="upload-progress" style="margin-bottom:1rem;"></div>`;
        }
        
        html += `<div class="section-title">📦 Content Queue (${data.length} รูปรอโพสต์)</div>`;
        if (!data.length) { document.getElementById('content-area').innerHTML = html + '<div class="empty-state"><div class="icon">📸</div><p>Queue ว่าง</p></div>'; setupDropZone(); return; }
        html += '<div class="table-wrap"><table><thead><tr><th>#</th><th>Type</th><th>File ID</th><th>วันที่</th><th></th></tr></thead><tbody>';
        data.forEach((c, i) => {
            html += `<tr><td>${i+1}</td><td>${esc(c.file_type)}</td><td style="font-family:var(--font-mono);font-size:0.75rem;">${esc((c.file_id || '').slice(0,30))}...</td><td>${fmtDateTime(c.created_at)}</td>
                <td>${hasRole('admin') ? `<button class="btn btn-sm btn-danger" onclick="deleteQueueItem(${c.id})">🗑</button>` : ''}</td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('content-area').innerHTML = html;
        setupDropZone();
    } catch (e) { toast(e.message, 'error'); }
}

function setupDropZone() {
    const zone = document.getElementById('upload-zone');
    if (!zone) return;
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', (e) => {
        e.preventDefault(); zone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleContentUpload(e.dataTransfer.files);
    });
}

async function handleContentUpload(files) {
    const progress = document.getElementById('upload-progress');
    if (!progress) return;
    let successCount = 0;
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        progress.innerHTML = `<div style="color:var(--primary);font-size:0.85rem;">⏳ กำลังอัพโหลด ${esc(file.name)} (${i+1}/${files.length})...</div>`;
        try {
            const formData = new FormData();
            formData.append('file', file);
            const headers = {};
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const resp = await fetch('/api/content/upload', { method: 'POST', headers, body: formData });
            if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.detail || 'Upload failed'); }
            successCount++;
        } catch (e) { toast(`❌ ${file.name}: ${e.message}`, 'error'); }
    }
    progress.innerHTML = '';
    if (successCount > 0) {
        toast(`✅ อัพโหลดสำเร็จ ${successCount} ไฟล์`, 'success');
        loadContentQueue();
    }
}

async function deleteQueueItem(id) {
    if (!confirm('ลบ?')) return;
    await api(`/content/queue/${id}`, { method: 'DELETE' }); loadContentQueue();
}

async function loadContentSchedule() {
    try {
        const data = await api('/content/schedule');
        let html = '<div class="table-wrap"><table><thead><tr><th>เวลา</th><th>กลุ่ม</th><th>Type</th><th>สถานะ</th></tr></thead><tbody>';
        data.forEach(s => {
            html += `<tr><td>${fmtDateTime(s.scheduled_at)}</td><td>${esc(s.group_slug)}</td><td>${esc(s.content_type)}</td>
                <td>${s.is_sent ? '<span style="color:var(--success)">✅ ส่งแล้ว</span>' : '<span style="color:var(--warning)">⏳ รอ</span>'}</td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('content-area').innerHTML = data.length ? html : '<div class="empty-state"><div class="icon">📅</div><p>ไม่มี schedule</p></div>';
    } catch (e) { toast(e.message, 'error'); }
}

async function loadContentStats() {
    try {
        const data = await api('/content/teaser-stats?days=30');
        let html = '<div class="section-title">📊 สถิติ Teaser (30 วัน)</div>';
        html += '<div class="table-wrap"><table><thead><tr><th>วันที่</th><th>คลิก</th></tr></thead><tbody>';
        (data.clicks || []).forEach(c => {
            html += `<tr><td>${fmtDate(c.date)}</td><td>${c.clicks}</td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('content-area').innerHTML = html;
    } catch (e) { toast(e.message, 'error'); }
}

// ========== PAGE: GROUPS ==========
async function renderGroups() {
    const content = document.getElementById('page-content');
    try {
        const data = await api('/groups/categorized');
        
        function groupTable(groups, emoji, title, category) {
            let html = `<div class="category-section"><div class="category-header">${emoji} ${esc(title)} <span class="category-count">${groups.length}</span>
                <button class="btn btn-sm btn-primary" style="margin-left:auto;" onclick="showAddGroupForm('${category}')">+ เพิ่มกลุ่ม</button>
            </div>`;
            if (!groups.length) { html += '<div class="empty-state" style="padding:1rem;">ไม่มีกลุ่ม</div></div>'; return html; }
            html += '<div class="table-wrap"><table><thead><tr><th>Slug</th><th>ชื่อกลุ่ม</th><th>Chat ID</th><th>Tier</th><th>สถานะ</th><th></th></tr></thead><tbody>';
            groups.forEach(g => {
                html += `<tr>
                    <td style="font-family:var(--font-mono);color:var(--primary);">${esc(g.slug)}</td>
                    <td>${esc(g.title)}</td>
                    <td style="font-family:var(--font-mono);font-size:0.8rem;">${g.chat_id}</td>
                    <td>${esc(g.min_tier)}</td>
                    <td>${g.is_active ? '<span style="color:var(--success)">Active</span>' : 'Off'}</td>
                    <td><div class="btn-group">
                        <button class="btn btn-sm btn-outline" onclick="showEditGroupForm(${g.id},'${esc(g.slug).replace(/'/g,'\\&#39;')}','${esc(g.title).replace(/'/g,'\\&#39;')}',${g.chat_id},'${esc(g.min_tier).replace(/'/g,'\\&#39;')}',${g.is_active})">✏️</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteGroup(${g.id})">🗑️</button>
                        <button class="btn btn-sm btn-outline" onclick="showGroupDetail(${g.id})">📋</button>
                        <button class="btn btn-sm btn-primary" onclick="genInviteLink(${g.id})">🔗</button>
                    </div></td></tr>`;
            });
            html += '</tbody></table></div></div>';
            return html;
        }
        
        content.innerHTML = 
            groupTable(data.vip, '👑', 'กลุ่ม VIP', 'vip') +
            groupTable(data.free, '🆓', 'กลุ่มฟรี', 'free') +
            groupTable(data.chat, '💬', 'กลุ่มพูดคุย', 'chat');
        
    } catch (e) { content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
}

function showAddGroupForm(category) {
    const defaultTier = category === 'vip' ? 'TIER_300' : category === 'free' ? 'FREE' : 'FREE';
    openModal('+ เพิ่มกลุ่ม', `
        <div class="form-group"><label>Slug</label><input id="grp-slug" placeholder="เช่น FREE12, G300"></div>
        <div class="form-group"><label>ชื่อกลุ่ม</label><input id="grp-title" placeholder="ชื่อกลุ่ม"></div>
        <div class="form-group"><label>Chat ID</label><input id="grp-chatid" type="number" placeholder="-100xxxxxxxxxx"></div>
        <div class="form-group"><label>Tier</label>
            <select id="grp-tier">
                <option value="FREE" ${defaultTier==='FREE'?'selected':''}>FREE</option>
                <option value="TIER_99" ${defaultTier==='TIER_99'?'selected':''}>TIER_99</option>
                <option value="TIER_300" ${defaultTier==='TIER_300'?'selected':''}>TIER_300</option>
                <option value="TIER_500" ${defaultTier==='TIER_500'?'selected':''}>TIER_500</option>
                <option value="TIER_1299" ${defaultTier==='TIER_1299'?'selected':''}>TIER_1299</option>
                <option value="TIER_2499" ${defaultTier==='TIER_2499'?'selected':''}>TIER_2499</option>
            </select>
        </div>
        <button class="btn btn-primary btn-full" onclick="createGroup()">💾 เพิ่มกลุ่ม</button>
    `);
}

async function createGroup() {
    try {
        await api('/groups', { method: 'POST', body: JSON.stringify({
            slug: document.getElementById('grp-slug').value.toUpperCase(),
            title: document.getElementById('grp-title').value,
            chat_id: parseInt(document.getElementById('grp-chatid').value),
            min_tier: document.getElementById('grp-tier').value,
            is_active: true,
        })});
        toast('เพิ่มกลุ่มสำเร็จ', 'success'); closeModal(); renderGroups();
    } catch (e) { toast(e.message, 'error'); }
}

function showEditGroupForm(id, slug, title, chatId, tier, isActive) {
    openModal('✏️ แก้ไขกลุ่ม: ' + slug, `
        <div class="form-group"><label>ชื่อกลุ่ม</label><input id="egrp-title" value="${esc(title)}"></div>
        <div class="form-group"><label>Chat ID</label><input id="egrp-chatid" type="number" value="${chatId}"></div>
        <div class="form-group"><label>Tier</label>
            <select id="egrp-tier">
                <option value="FREE" ${tier==='FREE'?'selected':''}>FREE</option>
                <option value="TIER_99" ${tier==='TIER_99'?'selected':''}>TIER_99</option>
                <option value="TIER_300" ${tier==='TIER_300'?'selected':''}>TIER_300</option>
                <option value="TIER_500" ${tier==='TIER_500'?'selected':''}>TIER_500</option>
                <option value="TIER_1299" ${tier==='TIER_1299'?'selected':''}>TIER_1299</option>
                <option value="TIER_2499" ${tier==='TIER_2499'?'selected':''}>TIER_2499</option>
            </select>
        </div>
        <div class="form-group"><label>สถานะ</label>
            <select id="egrp-active"><option value="true" ${isActive?'selected':''}>Active</option><option value="false" ${!isActive?'selected':''}>Inactive</option></select>
        </div>
        <button class="btn btn-primary btn-full" onclick="updateGroup(${id})">💾 บันทึก</button>
    `);
}

async function updateGroup(id) {
    try {
        await api(`/groups/${id}`, { method: 'PUT', body: JSON.stringify({
            title: document.getElementById('egrp-title').value,
            chat_id: parseInt(document.getElementById('egrp-chatid').value),
            min_tier: document.getElementById('egrp-tier').value,
            is_active: document.getElementById('egrp-active').value === 'true',
        })});
        toast('อัพเดตกลุ่มสำเร็จ', 'success'); closeModal(); renderGroups();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteGroup(id) {
    if (!confirm('ลบกลุ่มนี้?')) return;
    try {
        await api(`/groups/${id}`, { method: 'DELETE' });
        toast('ลบกลุ่มแล้ว', 'success'); renderGroups();
    } catch (e) { toast(e.message, 'error'); }
}

async function showGroupDetail(id) {
    try {
        const members = await api(`/groups/${id}/members`);
        let html = '<div class="section-title">👥 สมาชิก</div>';
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>Telegram ID</th><th>แพ็กเกจ</th><th>หมดอายุ</th></tr></thead><tbody>';
        members.forEach(m => {
            html += `<tr><td>${m.username ? '@'+esc(m.username) : esc(m.first_name) || '-'}</td><td>${m.telegram_id}</td><td>${esc(m.package_name)}</td><td>${fmtDate(m.end_date)}</td></tr>`;
        });
        html += '</tbody></table></div>';
        openModal('📱 สมาชิกกลุ่ม', html);
    } catch (e) { toast(e.message, 'error'); }
}

async function genInviteLink(id) {
    if (_busy.has(`inv-${id}`)) return;
    _busy.add(`inv-${id}`);
    try {
        const result = await api(`/groups/${id}/invite-link`, { method: 'POST' });
        const link = result?.result?.invite_link || 'ไม่สามารถสร้าง link ได้';
        openModal('🔗 Invite Link', `<div style="word-break:break-all;font-family:var(--font-mono);color:var(--primary);padding:1rem;background:var(--bg);border-radius:var(--radius-sm);">${esc(link)}</div>
            <button class="btn btn-primary btn-full" style="margin-top:1rem;" onclick="navigator.clipboard.writeText('${esc(link).replace(/'/g,'\\&#39;')}');toast('คัดลอกแล้ว','success')">📋 Copy</button>`);
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete(`inv-${id}`); }
}

// ========== PAGE: TEAM ==========
async function renderTeam() {
    const content = document.getElementById('page-content');
    try {
        const data = await api('/team');
        let html = '';
        if (hasRole('owner') || hasRole('super_admin')) html += `<button class="btn btn-primary" onclick="showAddTeamForm()" style="margin-bottom:1rem;">+ เพิ่มทีมงาน</button>`;
        
        // Role hierarchy info
        html += `<div class="alert-box" style="margin-bottom:1rem;font-size:0.8rem;">
            <div class="alert-box-item">👑 <b>Owner (100)</b> — ทำได้ทุกอย่าง</div>
            <div class="alert-box-item">⚡ <b>Super Admin (75)</b> — ทุกอย่างยกเว้น จัดการ Owner + แก้ bot tokens</div>
            <div class="alert-box-item">🛡️ <b>Admin (50)</b> — อนุมัติ + จัดการลูกค้า + โปรโมชั่น</div>
            <div class="alert-box-item">📋 <b>Moderator (10)</b> — ดู + อนุมัติสลิป</div>
        </div>`;
        
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>Telegram ID</th><th>ยศ</th><th>สถานะ</th><th>Login ล่าสุด</th><th></th></tr></thead><tbody>';
        data.forEach(m => {
            const roleIcons = { owner: '👑', super_admin: '⚡', admin: '🛡️', moderator: '📋' };
            const roleIcon = roleIcons[m.role] || '📋';
            const myLevel = ROLE_LEVELS[admin.role] || 0;
            const theirLevel = ROLE_LEVELS[m.role] || 0;
            const canEdit = myLevel > theirLevel && m.role !== 'owner';
            html += `<tr>
                <td>${esc(m.display_name)}</td>
                <td style="font-family:var(--font-mono);">${m.telegram_id}</td>
                <td>${roleIcon} ${esc(m.role)}</td>
                <td>${m.is_active ? '<span style="color:var(--success)">🟢</span>' : '<span style="color:var(--error)">🔴</span>'}</td>
                <td>${fmtDateTime(m.last_login_at)}</td>
                <td>${canEdit ? `<div class="btn-group"><button class="btn btn-sm btn-outline" onclick="showEditTeam(${m.id},'${esc(m.display_name).replace(/'/g,'\\&#39;')}','${esc(m.role).replace(/'/g,'\\&#39;')}',${m.is_active})">✏️</button><button class="btn btn-sm btn-outline" onclick="showTeamActivity(${m.id})">📋</button></div>` : (m.role !== 'owner' ? `<button class="btn btn-sm btn-outline" onclick="showTeamActivity(${m.id})">📋</button>` : '')}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        content.innerHTML = html;
    } catch (e) { content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
}

function showAddTeamForm() {
    const roleOptions = hasRole('owner') 
        ? '<option value="moderator">📋 Moderator</option><option value="admin">🛡️ Admin</option><option value="super_admin">⚡ Super Admin</option><option value="owner">👑 Owner</option>'
        : '<option value="moderator">📋 Moderator</option><option value="admin">🛡️ Admin</option>';
    openModal('+ เพิ่มทีมงาน', `
        <div class="form-group"><label>Telegram ID</label><input id="tm-tid" type="number"></div>
        <div class="form-group"><label>ชื่อ</label><input id="tm-name"></div>
        <div class="form-group"><label>รหัสผ่าน</label><input id="tm-pwd" type="password"></div>
        <div class="form-group"><label>ยศ</label><select id="tm-role">${roleOptions}</select></div>
        <button class="btn btn-primary btn-full" onclick="addTeamMember()">💾 เพิ่ม</button>
    `);
}

async function addTeamMember() {
    if (_busy.has('addTeam')) return;
    _busy.add('addTeam');
    try {
        await api('/team', { method: 'POST', body: JSON.stringify({
            telegram_id: parseInt(document.getElementById('tm-tid').value),
            display_name: document.getElementById('tm-name').value,
            password: document.getElementById('tm-pwd').value,
            role: document.getElementById('tm-role').value,
        })});
        toast('เพิ่มทีมงานแล้ว', 'success'); closeModal(); renderTeam();
    } catch (e) { toast(e.message, 'error'); }
    finally { _busy.delete('addTeam'); }
}

function showEditTeam(id, name, role, isActive) {
    let roleOpts = `<option value="moderator" ${role==='moderator'?'selected':''}>📋 Moderator</option><option value="admin" ${role==='admin'?'selected':''}>🛡️ Admin</option>`;
    if (hasRole('owner')) roleOpts += `<option value="super_admin" ${role==='super_admin'?'selected':''}>⚡ Super Admin</option>`;
    openModal('✏️ แก้ไข ' + name, `
        <div class="form-group"><label>ยศ</label><select id="et-role">${roleOpts}</select></div>
        <div class="form-group"><label>สถานะ</label><select id="et-active"><option value="true" ${isActive?'selected':''}>Active</option><option value="false" ${!isActive?'selected':''}>Inactive</option></select></div>
        <div class="btn-group">
            <button class="btn btn-primary" onclick="updateTeam(${id})">💾 บันทึก</button>
            <button class="btn btn-outline" onclick="resetTeamPwd(${id})">🔑 Reset Password</button>
            <button class="btn btn-danger" onclick="deleteTeam(${id})">🗑 ลบ</button>
        </div>
    `);
}

async function updateTeam(id) {
    try {
        await api(`/team/${id}`, { method: 'PUT', body: JSON.stringify({
            role: document.getElementById('et-role').value,
            is_active: document.getElementById('et-active').value === 'true',
        })});
        toast('อัพเดตแล้ว', 'success'); closeModal(); renderTeam();
    } catch (e) { toast(e.message, 'error'); }
}

async function resetTeamPwd(id) {
    const pw = prompt('รหัสผ่านใหม่:');
    if (!pw) return;
    try {
        await api(`/team/${id}/password-reset`, { method: 'PUT', body: JSON.stringify({ new_password: pw }), headers: { 'Content-Type': 'application/json' } });
        toast('Reset password แล้ว', 'success');
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteTeam(id) {
    if (!confirm('ลบทีมงานนี้?')) return;
    try {
        await api(`/team/${id}`, { method: 'DELETE' });
        toast('ลบแล้ว', 'success'); closeModal(); renderTeam();
    } catch (e) { toast(e.message, 'error'); }
}

async function showTeamActivity(id) {
    try {
        const data = await api(`/team/${id}/activity`);
        let html = '<div class="table-wrap"><table><thead><tr><th>เวลา</th><th>Action</th><th>Type</th><th>Details</th></tr></thead><tbody>';
        data.items.forEach(a => {
            const details = a.details ? JSON.stringify(a.details).slice(0,80) : '-';
            html += `<tr><td>${fmtDateTime(a.created_at)}</td><td>${esc(a.action)}</td><td>${esc(a.entity_type || '-')}</td><td style="font-size:0.8rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;">${esc(details)}</td></tr>`;
        });
        html += '</tbody></table></div>';
        openModal('📋 Activity Log', html);
    } catch (e) { toast(e.message, 'error'); }
}

// ========== PAGE: SETTINGS ==========
let settingsTab = 'packages';
async function renderSettings() {
    const content = document.getElementById('page-content');
    content.innerHTML = `
        <div class="tabs">
            <div class="tab ${settingsTab==='packages'?'active':''}" onclick="settingsTab='packages';renderSettings()">📦 แพ็กเกจ</div>
            ${hasRole('owner') ? `<div class="tab ${settingsTab==='bots'?'active':''}" onclick="settingsTab='bots';renderSettings()">🤖 Bots</div>` : ''}
            ${hasRole('super_admin') && !hasRole('owner') ? `<div class="tab ${settingsTab==='bots'?'active':''}" onclick="settingsTab='bots';renderSettings()">🤖 Bots (ดูอย่างเดียว)</div>` : ''}
            <div class="tab ${settingsTab==='dm'?'active':''}" onclick="settingsTab='dm';renderSettings()">📩 DM</div>
        </div>
        <div id="settings-area"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (settingsTab === 'packages') loadPackages();
    else if (settingsTab === 'bots') loadBotSettings();
    else if (settingsTab === 'dm') loadDMSettings();
}

async function loadPackages() {
    try {
        const data = await api('/settings/packages');
        let html = '';
        if (hasRole('owner')) html += `<button class="btn btn-primary" onclick="showPkgForm()" style="margin-bottom:1rem;">+ เพิ่มแพ็กเกจ</button>`;
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>ราคา</th><th>วัน</th><th>Tier</th><th>สถานะ</th><th></th></tr></thead><tbody>';
        data.forEach(p => {
            html += `<tr><td>${esc(p.name)}</td><td>${fmtBaht(p.price)}</td><td>${p.duration_days}</td><td>${esc(p.tier)}</td>
                <td>${p.is_active ? '<span style="color:var(--success)">Active</span>' : 'Off'}</td>
                <td>${hasRole('owner') ? `<button class="btn btn-sm btn-outline" onclick="editPkg(${p.id},'${esc(p.name).replace(/'/g,'\\&#39;')}',${p.price},${p.duration_days})">✏️</button>` : ''}</td></tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('settings-area').innerHTML = html;
    } catch (e) { toast(e.message, 'error'); }
}

function showPkgForm() {
    openModal('+ เพิ่มแพ็กเกจ', `
        <div class="form-group"><label>ชื่อ</label><input id="pkg-name"></div>
        <div class="form-row">
            <div class="form-group"><label>ราคา</label><input id="pkg-price" type="number"></div>
            <div class="form-group"><label>วัน</label><input id="pkg-days" type="number"></div>
        </div>
        <div class="form-group"><label>Tier</label><select id="pkg-tier"><option value="TIER_99">TIER_99</option><option value="TIER_300">TIER_300</option><option value="TIER_500">TIER_500</option><option value="TIER_1299">TIER_1299</option><option value="TIER_2499">TIER_2499</option></select></div>
        <button class="btn btn-primary btn-full" onclick="createPkg()">💾 สร้าง</button>
    `);
}

async function createPkg() {
    try {
        await api('/settings/packages', { method: 'POST', body: JSON.stringify({
            name: document.getElementById('pkg-name').value,
            price: parseFloat(document.getElementById('pkg-price').value),
            duration_days: parseInt(document.getElementById('pkg-days').value),
            tier: document.getElementById('pkg-tier').value,
        })});
        toast('สร้างแล้ว', 'success'); closeModal(); loadPackages();
    } catch (e) { toast(e.message, 'error'); }
}

function editPkg(id, name, price, days) {
    openModal('✏️ แก้ไข ' + name, `
        <div class="form-group"><label>ชื่อ</label><input id="epkg-name" value="${esc(name)}"></div>
        <div class="form-row">
            <div class="form-group"><label>ราคา</label><input id="epkg-price" type="number" value="${price}"></div>
            <div class="form-group"><label>วัน</label><input id="epkg-days" type="number" value="${days}"></div>
        </div>
        <button class="btn btn-primary btn-full" onclick="updatePkg(${id})">💾 บันทึก</button>
    `);
}

async function updatePkg(id) {
    try {
        await api(`/settings/packages/${id}`, { method: 'PUT', body: JSON.stringify({
            name: document.getElementById('epkg-name').value,
            price: parseFloat(document.getElementById('epkg-price').value),
            duration_days: parseInt(document.getElementById('epkg-days').value),
        })});
        toast('อัพเดตแล้ว', 'success'); closeModal(); loadPackages();
    } catch (e) { toast(e.message, 'error'); }
}

async function loadBotSettings() {
    try {
        const data = await api('/settings/bots');
        const botNames = { sales: '🛒 Sales Bot', guardian: '🛡️ Guardian Bot', admin: '⚙️ Admin Bot', content: '📸 Content Bot', announce: '📢 Announce Bot' };
        let html = '<div class="section-title">🤖 Bot Tokens</div>';
        Object.entries(data).forEach(([name, maskedToken]) => {
            html += `<div class="detail-row" id="bot-row-${name}">
                <span class="detail-label">${botNames[name] || name}</span>
                <span class="detail-value" style="display:flex;align-items:center;gap:0.5rem;">
                    <span id="bot-display-${name}" style="font-family:var(--font-mono);">${maskedToken}</span>
                    <input type="text" id="bot-input-${name}" placeholder="ใส่ token ใหม่..." style="display:none;width:320px;padding:0.4rem 0.6rem;font-size:0.8rem;">
                    <button class="btn btn-sm btn-outline" id="bot-edit-${name}" onclick="startEditToken('${name}')">✏️ แก้ไข</button>
                    <button class="btn btn-sm btn-success" id="bot-save-${name}" style="display:none;" onclick="saveToken('${name}')">💾 บันทึก</button>
                    <button class="btn btn-sm btn-outline" id="bot-cancel-${name}" style="display:none;" onclick="cancelEditToken('${name}')">✕</button>
                </span>
            </div>`;
        });
        html += '<div style="margin-top:1rem;font-size:0.8rem;color:var(--text-dim);">⚠️ หลังแก้ token ต้อง restart bot container เพื่อให้มีผล</div>';
        document.getElementById('settings-area').innerHTML = `<div class="detail-panel">${html}</div>`;
    } catch (e) { toast(e.message, 'error'); }
}

function startEditToken(name) {
    document.getElementById(`bot-display-${name}`).style.display = 'none';
    document.getElementById(`bot-edit-${name}`).style.display = 'none';
    document.getElementById(`bot-input-${name}`).style.display = '';
    document.getElementById(`bot-save-${name}`).style.display = '';
    document.getElementById(`bot-cancel-${name}`).style.display = '';
    document.getElementById(`bot-input-${name}`).focus();
}

function cancelEditToken(name) {
    document.getElementById(`bot-display-${name}`).style.display = '';
    document.getElementById(`bot-edit-${name}`).style.display = '';
    document.getElementById(`bot-input-${name}`).style.display = 'none';
    document.getElementById(`bot-save-${name}`).style.display = 'none';
    document.getElementById(`bot-cancel-${name}`).style.display = 'none';
    document.getElementById(`bot-input-${name}`).value = '';
}

async function saveToken(name) {
    const newToken = document.getElementById(`bot-input-${name}`).value.trim();
    if (!newToken || !newToken.includes(':')) { toast('Token ไม่ถูกต้อง (ต้องมี :)', 'error'); return; }
    try {
        await api('/settings/bot-token', { method: 'PUT', body: JSON.stringify({ name, token: newToken }) });
        toast(`✅ อัพเดต ${name} token สำเร็จ`, 'success');
        loadBotSettings();
    } catch (e) { toast(e.message, 'error'); }
}

async function loadDMSettings() {
    try {
        const data = await api('/settings/dm');
        
        // Try to get DM stats
        let dmStats = { comeback_sent: 0, comeback_respond: 0, comeback_convert: 0, trial_sent: 0, trial_click: 0, trial_convert: 0 };
        try { dmStats = await api('/dashboard/dm-stats'); } catch {}
        
        document.getElementById('settings-area').innerHTML = `
            <div class="section-title">📊 สถิติ DM วันนี้</div>
            <div class="dm-stats-grid">
                <div class="mini-card"><div class="mini-card-label">Comeback ส่ง</div><div class="mini-card-value" style="color:var(--primary);">${dmStats.comeback_sent || 0}</div></div>
                <div class="mini-card"><div class="mini-card-label">Comeback ตอบ</div><div class="mini-card-value" style="color:var(--success);">${dmStats.comeback_respond || 0}</div></div>
                <div class="mini-card"><div class="mini-card-label">Comeback สมัคร</div><div class="mini-card-value" style="color:var(--warning);">${dmStats.comeback_convert || 0}</div></div>
                <div class="mini-card"><div class="mini-card-label">Trial ส่ง</div><div class="mini-card-value" style="color:var(--primary);">${dmStats.trial_sent || 0}</div></div>
                <div class="mini-card"><div class="mini-card-label">Trial คลิก</div><div class="mini-card-value" style="color:var(--success);">${dmStats.trial_click || 0}</div></div>
                <div class="mini-card"><div class="mini-card-label">Trial สมัคร</div><div class="mini-card-value" style="color:var(--warning);">${dmStats.trial_convert || 0}</div></div>
            </div>
            
            <div class="detail-panel" style="margin-top:1rem;">
                <div class="section-title">📩 DM Settings</div>
                
                <div class="detail-row">
                    <div>
                        <span class="detail-label">COMEBACK DM / วัน</span>
                        <div class="dm-description">ส่ง DM ให้ลูกค้าที่หมดอายุ > 3 วัน พร้อมส่วนลดชวนกลับมา</div>
                    </div>
                    <span class="detail-value">${data.comeback_per_day}</span>
                </div>
                <div class="detail-row">
                    <div>
                        <span class="detail-label">Comeback Delay (วินาที)</span>
                        <div class="dm-description">หน่วงเวลาระหว่างแต่ละ DM (วินาที) กัน Telegram ban</div>
                    </div>
                    <span class="detail-value">${data.comeback_delay}s</span>
                </div>
                <div class="detail-row">
                    <div>
                        <span class="detail-label">Trial DM / วัน</span>
                        <div class="dm-description">ส่ง DM ให้คนที่ไม่เคยจ่ายเงิน ชวนทดลอง Trial ฿99/24ชม.</div>
                    </div>
                    <span class="detail-value">${data.trial_per_day}</span>
                </div>
                <div class="detail-row">
                    <div>
                        <span class="detail-label">Trial Delay (วินาที)</span>
                        <div class="dm-description">หน่วงเวลาระหว่างแต่ละ DM (วินาที) กัน Telegram ban</div>
                    </div>
                    <span class="detail-value">${data.trial_delay}s</span>
                </div>
            </div>
            
            <div class="btn-group" style="margin-top:1rem;">
                <button class="btn btn-primary" onclick="testDM('comeback')">▶️ ทดสอบส่ง Comeback 1 คน</button>
                <button class="btn btn-primary" onclick="testDM('trial')">▶️ ทดสอบส่ง Trial 1 คน</button>
            </div>
        `;
    } catch (e) { toast(e.message, 'error'); }
}

async function testDM(type) {
    if (!confirm(`ทดสอบส่ง ${type} DM ให้ 1 คน?`)) return;
    try {
        const result = await api(`/marketing/test-dm?type=${type}`, { method: 'POST' });
        toast(`✅ ส่ง ${type} DM ทดสอบสำเร็จ: ${result.message || 'sent'}`, 'success');
    } catch (e) { toast(e.message, 'error'); }
}

// ========== PAGE: MARKETING ==========
async function renderMarketing() {
    const content = document.getElementById('page-content');
    try {
        const [kpi, funnel, weekly, insights, roi, links] = await Promise.all([
            api('/marketing/kpi?days=30'),
            api('/marketing/funnel?days=30'),
            api('/marketing/weekly-comparison'),
            api('/marketing/ai-insights'),
            api('/marketing/roi?days=30').catch(() => null),
            api('/marketing/links').catch(() => []),
        ]);
        
        const funnelMax = funnel.free_members || 1;
        
        content.innerHTML = `
            <div class="mini-cards">
                <div class="mini-card"><div class="mini-card-label">Revenue (30d)</div><div class="mini-card-value" style="color:var(--primary);">${fmtBaht(kpi.revenue)}</div></div>
                <div class="mini-card"><div class="mini-card-label">New Members</div><div class="mini-card-value" style="color:var(--success);">${fmt(kpi.new_members)}</div></div>
                <div class="mini-card"><div class="mini-card-label">Churned</div><div class="mini-card-value" style="color:var(--error);">${fmt(kpi.churned)}</div></div>
                <div class="mini-card"><div class="mini-card-label">Active</div><div class="mini-card-value">${fmt(kpi.active_members)}</div></div>
            </div>

            <div class="card card-full" style="margin-bottom:1.5rem;">
                <div class="card-label">📈 สัปดาห์ vs สัปดาห์</div>
                <div class="chart-container"><canvas id="weekly-chart"></canvas></div>
            </div>

            <div class="section-title">🔄 Conversion Funnel (30 วัน)</div>
            <div class="funnel">
                <div class="funnel-step">
                    <span class="funnel-label">กลุ่มฟรี (${fmt(funnel.free_members)} คน)</span>
                    <div class="funnel-bar" style="width:100%;">${fmt(funnel.free_members)}</div>
                    <span class="funnel-pct">100%</span>
                </div>
                <div class="funnel-step">
                    <span class="funnel-label">คลิก Teaser (${fmt(funnel.teaser_clicks)} คน)</span>
                    <div class="funnel-bar" style="width:${Math.max(5, (funnel.teaser_clicks/funnelMax)*100)}%;">${fmt(funnel.teaser_clicks)}</div>
                    <span class="funnel-pct">${((funnel.teaser_clicks/funnelMax)*100).toFixed(1)}%</span>
                </div>
                <div class="funnel-step">
                    <span class="funnel-label">ซื้อ Trial (${fmt(funnel.trial_purchases)} คน)</span>
                    <div class="funnel-bar" style="width:${Math.max(3, (funnel.trial_purchases/funnelMax)*100)}%;">${fmt(funnel.trial_purchases)}</div>
                    <span class="funnel-pct">${((funnel.trial_purchases/funnelMax)*100).toFixed(1)}%</span>
                </div>
                <div class="funnel-step">
                    <span class="funnel-label">ซื้อ VIP/GOD (${fmt(funnel.vip_purchases)} คน)</span>
                    <div class="funnel-bar" style="width:${Math.max(2, (funnel.vip_purchases/funnelMax)*100)}%;">${fmt(funnel.vip_purchases)}</div>
                    <span class="funnel-pct">${((funnel.vip_purchases/funnelMax)*100).toFixed(1)}%</span>
                </div>
            </div>


            ${roi ? `
            <div class="section-title" style="margin-top:1.5rem;">🎯 ROI ทีมการตลาด (30 วัน)</div>
            
            <div class="mini-cards">
                <div class="mini-card"><div class="mini-card-label">ค่าโฆษณารวม</div><div class="mini-card-value" style="color:var(--warning);">${fmtBaht(roi.totals.cost)}</div></div>
                <div class="mini-card"><div class="mini-card-label">รายได้</div><div class="mini-card-value" style="color:var(--primary);">${fmtBaht(roi.totals.revenue)}</div></div>
                <div class="mini-card"><div class="mini-card-label">กำไรสุทธิ</div><div class="mini-card-value" style="color:${roi.totals.profit >= 0 ? 'var(--success)' : 'var(--error)'};">${fmtBaht(roi.totals.profit)}</div></div>
                <div class="mini-card"><div class="mini-card-label">ROI</div><div class="mini-card-value" style="color:${(roi.totals.roi_pct || 0) >= 0 ? 'var(--success)' : 'var(--error)'};">${roi.totals.roi_pct != null ? roi.totals.roi_pct + '%' : '-'}</div></div>
            </div>

            ${roi.by_marketer.length > 0 ? `
            <div class="card card-full" style="margin-top:1rem;">
                <div class="card-label">👥 แยกตาม Marketer</div>
                <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;color:var(--text);font-size:0.9rem;">
                    <thead style="background:rgba(0,212,255,0.08);">
                        <tr>
                            <th style="padding:0.6rem;text-align:left;">Marketer</th>
                            <th style="padding:0.6rem;text-align:right;">ค่าโฆษณา</th>
                            <th style="padding:0.6rem;text-align:right;">Joins</th>
                            <th style="padding:0.6rem;text-align:right;">Paid</th>
                            <th style="padding:0.6rem;text-align:right;">รายได้</th>
                            <th style="padding:0.6rem;text-align:right;">กำไร</th>
                            <th style="padding:0.6rem;text-align:right;">ROI</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${roi.by_marketer.map(m => `
                        <tr style="border-top:1px solid var(--border);">
                            <td style="padding:0.6rem;font-weight:500;">${esc(m.marketer)}</td>
                            <td style="padding:0.6rem;text-align:right;color:var(--warning);">${fmtBaht(m.cost)}</td>
                            <td style="padding:0.6rem;text-align:right;">${fmt(m.joins)}</td>
                            <td style="padding:0.6rem;text-align:right;color:var(--success);">${fmt(m.paid)}</td>
                            <td style="padding:0.6rem;text-align:right;color:var(--primary);">${fmtBaht(m.revenue)}</td>
                            <td style="padding:0.6rem;text-align:right;color:${m.profit >= 0 ? 'var(--success)' : 'var(--error)'};">${fmtBaht(m.profit)}</td>
                            <td style="padding:0.6rem;text-align:right;font-weight:500;color:${(m.roi_pct||0) >= 100 ? 'var(--success)' : (m.roi_pct||0) >= 0 ? 'var(--warning)' : 'var(--error)'};">${m.roi_pct != null ? m.roi_pct + '%' : '-'}</td>
                        </tr>
                        `).join('')}
                    </tbody>
                </table>
                </div>
            </div>
            ` : ''}

            ${roi.by_platform.length > 0 ? `
            <div class="card card-full" style="margin-top:1rem;">
                <div class="card-label">📱 แยกตาม Platform</div>
                <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;color:var(--text);font-size:0.85rem;">
                    <thead style="background:rgba(0,212,255,0.08);">
                        <tr>
                            <th style="padding:0.6rem;text-align:left;">Marketer/Platform</th>
                            <th style="padding:0.6rem;text-align:right;">Cost</th>
                            <th style="padding:0.6rem;text-align:right;">Joins</th>
                            <th style="padding:0.6rem;text-align:right;">Paid</th>
                            <th style="padding:0.6rem;text-align:right;">Revenue</th>
                            <th style="padding:0.6rem;text-align:right;">ROI</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${roi.by_platform.map(p => `
                        <tr style="border-top:1px solid var(--border);">
                            <td style="padding:0.6rem;"><span style="opacity:0.8;">${esc(p.marketer)}</span> / <b>${esc(p.platform)}</b></td>
                            <td style="padding:0.6rem;text-align:right;">${fmtBaht(p.cost)}</td>
                            <td style="padding:0.6rem;text-align:right;">${fmt(p.joins)}</td>
                            <td style="padding:0.6rem;text-align:right;color:var(--success);">${fmt(p.paid)}</td>
                            <td style="padding:0.6rem;text-align:right;color:var(--primary);">${fmtBaht(p.revenue)}</td>
                            <td style="padding:0.6rem;text-align:right;font-weight:500;color:${(p.roi_pct||0) >= 100 ? 'var(--success)' : (p.roi_pct||0) >= 0 ? 'var(--warning)' : 'var(--error)'};">${p.roi_pct != null ? p.roi_pct + '%' : '-'}</td>
                        </tr>
                        `).join('')}
                    </tbody>
                </table>
                </div>
            </div>
            ` : ''}

            ${links.length > 0 ? `
            <div class="card card-full" style="margin-top:1rem;">
                <div class="card-label">🔗 ลิ้งทั้งหมด (${links.length})</div>
                <div style="overflow-x:auto;max-height:400px;overflow-y:auto;">
                <table style="width:100%;border-collapse:collapse;color:var(--text);font-size:0.8rem;">
                    <thead style="background:rgba(0,212,255,0.08);position:sticky;top:0;">
                        <tr>
                            <th style="padding:0.5rem;text-align:left;">ID</th>
                            <th style="padding:0.5rem;text-align:left;">Marketer</th>
                            <th style="padding:0.5rem;text-align:left;">Platform</th>
                            <th style="padding:0.5rem;text-align:right;">Cost</th>
                            <th style="padding:0.5rem;text-align:right;">Joins</th>
                            <th style="padding:0.5rem;text-align:right;">Revenue</th>
                            <th style="padding:0.5rem;text-align:right;">Profit</th>
                            <th style="padding:0.5rem;text-align:center;">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${links.map(l => `
                        <tr style="border-top:1px solid var(--border);${l.is_revoked ? 'opacity:0.4;' : ''}">
                            <td style="padding:0.5rem;font-family:var(--font-mono,monospace);">#${l.id}</td>
                            <td style="padding:0.5rem;">${esc(l.marketer)}</td>
                            <td style="padding:0.5rem;">${esc(l.platform)}</td>
                            <td style="padding:0.5rem;text-align:right;">${l.cost > 0 ? fmtBaht(l.cost) : '<span style="color:var(--text-muted);">-</span>'}</td>
                            <td style="padding:0.5rem;text-align:right;">${fmt(l.joins)}</td>
                            <td style="padding:0.5rem;text-align:right;color:var(--primary);">${fmtBaht(l.revenue)}</td>
                            <td style="padding:0.5rem;text-align:right;color:${l.profit >= 0 ? 'var(--success)' : 'var(--error)'};">${fmtBaht(l.profit)}</td>
                            <td style="padding:0.5rem;text-align:center;">${l.is_revoked ? '🔴' : '🟢'}</td>
                        </tr>
                        `).join('')}
                    </tbody>
                </table>
                </div>
                <div style="font-size:0.75rem;color:var(--text-muted);padding:0.5rem 0;margin-top:0.5rem;">
                    💡 ใส่ค่าโฆษณาผ่าน Discord: พิมพ์ <code>cost &lt;id&gt; &lt;amount&gt;</code> ใน #ivy / #wasu / #pai
                </div>
            </div>
            ` : ''}
            ` : ''}

            <div class="card card-full" style="margin-top:1.5rem;">
                <div class="card-label">🤖 AI Action Items</div>
                <div style="padding:1rem;white-space:pre-wrap;color:var(--text);font-size:0.9rem;">${insights.insights || 'ยังไม่มี'}</div>
                ${insights.date ? `<div style="font-size:0.75rem;color:var(--text-dim);padding:0 1rem 1rem;">อัพเดต: ${insights.date}</div>` : ''}
            </div>
        `;

        // Weekly chart
        if (weekly.length >= 1) {
            charts.weekly = new Chart(document.getElementById('weekly-chart'), {
                type: 'bar',
                data: {
                    labels: weekly.map(w => w.week),
                    datasets: [{
                        label: 'รายได้ (฿)',
                        data: weekly.map(w => w.revenue),
                        backgroundColor: 'rgba(0, 212, 255, 0.6)',
                        borderColor: '#00d4ff',
                        borderWidth: 1,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { ticks: { color: '#8892b0' }, grid: { color: 'rgba(35,53,84,0.3)' } },
                        y: { ticks: { color: '#8892b0', callback: v => '฿' + fmt(v) }, grid: { color: 'rgba(35,53,84,0.3)' } },
                    },
                    plugins: { legend: { labels: { color: '#e0e6f0' } } },
                }
            });
        } else {
            const weeklyEl = document.getElementById('weekly-chart');
            if (weeklyEl) weeklyEl.parentElement.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);font-size:0.9rem;">📊 ยังไม่มีข้อมูลรายสัปดาห์</div>';
        }
    } catch (e) { content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(e.message)}</p></div>`; }
}

// ========== PAGE: ACTIVITY LOG ==========
let activityPage = 1, activityAction = '', activityAdmin = 0;
async function renderActivityLog() {
    const content = document.getElementById('page-content');
    try {
        const filters = await api('/dashboard/activity-log/filters');
        const actionOpts = '<option value="">ทั้งหมด</option>' + filters.actions.map(a => `<option value="${a}" ${activityAction===a?'selected':''}>${a}</option>`).join('');
        const adminOpts = '<option value="0">ทั้งหมด</option>' + filters.admins.map(a => `<option value="${a.id}" ${activityAdmin==a.id?'selected':''}>${esc(a.name)}</option>`).join('');
        content.innerHTML = `
            <div class="filters" style="margin-bottom:1rem;">
                <select onchange="activityAction=this.value;activityPage=1;loadActivityLog()" style="padding:0.5rem;border-radius:var(--radius-sm);background:var(--surface-2);color:var(--text);border:1px solid var(--border);">
                    ${actionOpts}
                </select>
                <select onchange="activityAdmin=parseInt(this.value);activityPage=1;loadActivityLog()" style="padding:0.5rem;border-radius:var(--radius-sm);background:var(--surface-2);color:var(--text);border:1px solid var(--border);">
                    ${adminOpts}
                </select>
            </div>
            <div id="activity-table"><div class="loading"><div class="spinner"></div></div></div>
            <div id="activity-pagination"></div>
        `;
        loadActivityLog();
    } catch (e) { content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(e.message)}</p></div>`; }
}

async function loadActivityLog(page) {
    if (page) activityPage = page;
    try {
        let url = `/dashboard/activity-log?page=${activityPage}&per_page=30`;
        if (activityAction) url += `&action=${encodeURIComponent(activityAction)}`;
        if (activityAdmin) url += `&admin_id=${activityAdmin}`;
        const data = await api(url);
        let html = '<div class="table-wrap"><table><thead><tr><th>เวลา</th><th>Admin</th><th>Action</th><th>Type</th><th>Entity ID</th><th>Details</th><th>IP</th></tr></thead><tbody>';
        data.items.forEach(a => {
            const details = a.details ? JSON.stringify(a.details).slice(0, 100) : '-';
            html += `<tr>
                <td style="white-space:nowrap;">${fmtDateTime(a.created_at)}</td>
                <td>${esc(a.admin_name || a.admin_id)}</td>
                <td><span class="status-badge">${esc(a.action)}</span></td>
                <td>${esc(a.entity_type || '-')}</td>
                <td>${a.entity_id || '-'}</td>
                <td style="font-size:0.8rem;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(details)}</td>
                <td style="font-size:0.75rem;font-family:var(--font-mono);">${esc(a.ip_address || '-')}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        document.getElementById('activity-table').innerHTML = data.items.length ? html : '<div class="empty-state"><div class="icon">📋</div><p>ไม่มี activity</p></div>';
        document.getElementById('activity-pagination').innerHTML = paginationHtml(data.page, data.pages, 'loadActivityLog');
    } catch (e) { toast(e.message, 'error'); }
}

// ========== INIT ==========
if (token && admin) {
    showApp();
} else {
    document.getElementById('login-page').classList.remove('hidden');
}

// ========== FIX 2025-05-21 (Phase D-XSS): Auto-logout on idle 30 min ==========
let _idleTimer;
function _resetIdleTimer() {
    clearTimeout(_idleTimer);
    _idleTimer = setTimeout(() => {
        if (token) {
            try { toast('Idle 30 นาที — auto logout', 'error'); } catch {}
            logout();
        }
    }, 30 * 60 * 1000);
}
['click','keydown','mousemove','touchstart'].forEach(e =>
    document.addEventListener(e, _resetIdleTimer, true)
);
_resetIdleTimer();
