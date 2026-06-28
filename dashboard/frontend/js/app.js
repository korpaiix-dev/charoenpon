// ===== Theme toggle (light/dark) =====
function applyTheme(theme) {
    const t = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('theme', t); } catch {}
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) btn.textContent = t === 'dark' ? '☀️' : '🌙';
    // Refresh chart defaults if loaded (safe even pre-init)
    try { if (typeof setupChartTheme === 'function') setupChartTheme(); } catch {}
}
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    applyTheme(current === 'light' ? 'dark' : 'light');
    // After toggle, re-render active page so charts adopt new theme
    // Wrapped in setTimeout so we never hit TDZ on `let currentPage`
    setTimeout(() => {
        try {
            if (typeof navigate === 'function' && typeof window.__currentPage !== 'undefined' && window.__currentPage) {
                navigate(window.__currentPage);
            } else if (typeof navigate === 'function') {
                // Fallback: read from URL or default to dashboard
                (restoreLastPage() || navigate('dashboard'));
            }
        } catch (e) { console.warn('theme rerender:', e); }
    }, 50);
}
// Init on page load
(function() {
    let saved = null;
    try { saved = localStorage.getItem('theme'); } catch {}
    applyTheme(saved || 'light');
})();

// ===== Chart.js theme-aware defaults (Geist-inspired) =====
function _cssVar(name) {
  try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch { return ""; }
}
function chartColors() {
  return {
    text:    _cssVar("--text-muted") || "#525252",
    textDim: _cssVar("--text-dim")   || "#8F8F8F",
    grid:    _cssVar("--border")     || "#EAEAEA",
    primary: _cssVar("--primary")    || "#F7B045",
    accent:  _cssVar("--accent")     || "#0070F3",
    success: _cssVar("--success")    || "#16A34A",
    error:   _cssVar("--error")      || "#DC2626",
    warning: _cssVar("--warning")    || "#D97706",
    surface: _cssVar("--surface")    || "#FFFFFF",
    textFull:_cssVar("--text")       || "#0A0A0A",
  };
}
function chartAlpha(hex, a) {
  const h = (hex || "").replace("#", "");
  if (h.length !== 6) return hex;
  const r = parseInt(h.slice(0,2), 16);
  const g = parseInt(h.slice(2,4), 16);
  const b = parseInt(h.slice(4,6), 16);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
function setupChartTheme() {
  if (typeof Chart === "undefined") return;
  const c = chartColors();
  Chart.defaults.color = c.text;
  Chart.defaults.borderColor = c.grid;
  Chart.defaults.font.family = _cssVar("--font") || "Inter, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.labels.color = c.text;
  Chart.defaults.plugins.legend.labels.font = { size: 12, weight: 500 };
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.tooltip.backgroundColor = c.surface;
  Chart.defaults.plugins.tooltip.titleColor = c.textFull;
  Chart.defaults.plugins.tooltip.bodyColor = c.text;
  Chart.defaults.plugins.tooltip.borderColor = c.grid;
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 6;
  Chart.defaults.plugins.tooltip.displayColors = true;
  Chart.defaults.plugins.tooltip.boxPadding = 4;
}
window.addEventListener("DOMContentLoaded", setupChartTheme);

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
    // ─── หน้าเปิด ───
    { id: 'today', icon: '📋', label: 'งานวันนี้', minRole: 'moderator' },

    // ─── งานเร่งด่วน ───
    { type: 'divider', label: 'งานเร่งด่วน' },
    { id: 'inbox', icon: '📥', label: 'Inbox สลิป', minRole: 'moderator' },
    { id: 'customers', icon: '👥', label: 'ลูกค้า', minRole: 'moderator' },

    // ─── สื่อสาร + ดึงดูด ───
    { type: 'divider', label: 'สื่อสาร + โปร' },
    { id: 'promotions', icon: '🎁', label: 'โปรโมชั่น + บอท', minRole: 'admin' },
    { id: 'journey', icon: '📨', label: 'DM อัตโนมัติ (Journey)', minRole: 'admin' },
    { id: 'content', icon: '📸', label: 'Content', minRole: 'moderator' },
    { id: 'gacha', icon: '🎰', label: 'กาชา', minRole: 'admin' },
    { id: 'prae_logs', icon: '💭', label: 'บทสนทนา Prae', minRole: 'admin' },

    // ─── การเงิน + ภาพรวม ───
    { type: 'divider', label: 'การเงิน + รายงาน' },
    { id: 'finance', icon: '💰', label: 'การเงิน + Receivers', minRole: 'moderator' },
    { id: 'receivers', icon: '💳', label: 'บัญชีรับเงิน', minRole: 'admin' },
    { id: 'dashboard', icon: '📊', label: 'ภาพรวม', minRole: 'moderator' },
    { id: 'marketing', icon: '📈', label: 'Marketing ROI', minRole: 'admin' },

    // ─── ดูแลระบบ ───
    { type: 'divider', label: 'ดูแลระบบ' },
    { id: 'team', icon: '👨‍💼', label: 'ทีมงาน', minRole: 'admin' },
    { id: 'groups', icon: '🏛', label: 'กลุ่ม VIP/ฟรี', minRole: 'admin' },
    { id: 'bot_groups', icon: '🤖', label: 'จัดการบอท', minRole: 'admin' },
    { id: 'group_analytics', icon: '📊', label: 'สถิติกลุ่ม', minRole: 'admin' },
    { id: 'bot_schedules', icon: '⏰', label: 'ตารางเวลาบอท', minRole: 'admin' },
    { id: 'content_editor', icon: '📝', label: 'คอนเทนต์บอท', minRole: 'admin' },
    { id: 'settings', icon: '⚙️', label: 'ตั้งค่าระบบ', minRole: 'admin' },

    // ─── ประวัติ ───
    { type: 'divider', label: 'ประวัติ' },
    { id: 'health', icon: '🚦', label: 'สถานะระบบ', minRole: 'admin' },
    { id: 'activity', icon: '📜', label: 'Activity Log', minRole: 'admin' },
];

// ========== API ==========
let _loggingOut = false;
async function api(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    
    // 2026-06-28: retry on network errors (e.g. brief dashboard restarts during hot reload)
    let resp = null;
    let _retryAttempts = 0;
    const MAX_RETRY = 3;
    while (true) {
        try {
            resp = await fetch(`/api${path}`, { ...options, headers: { ...headers, ...options.headers } });
            break; // got a response (even if not 2xx)
        } catch (netErr) {
            // TypeError "Failed to fetch" = network/connection error
            _retryAttempts++;
            if (_retryAttempts >= MAX_RETRY) throw netErr;
            await new Promise(r => setTimeout(r, 1500));
        }
    }
    if (resp.status === 401) {
        if (!_loggingOut) { _loggingOut = true; logout(); _loggingOut = false; }
        throw new Error('Session expired');
    }
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Error' }));
        let msg = err.detail || err.error || err.message || 'API Error';
        // FastAPI 422 returns array of {loc, msg, type} — extract first msg
        if (Array.isArray(msg)) {
            msg = msg.map(e => (e && e.msg) ? e.msg : JSON.stringify(e)).join('; ');
        } else if (typeof msg === 'object') {
            msg = JSON.stringify(msg);
        }
        throw new Error(String(msg));
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
    (restoreLastPage() || navigate('dashboard'));
    startAlertPolling();
    checkTestMode();  // Phase A.2
}

function renderSidebar() {
    const nav = document.getElementById('sidebar-nav');
    const level = ROLE_LEVELS[admin.role] || 0;
    nav.innerHTML = NAV_ITEMS
        .filter(item => item.type === 'divider' || level >= ROLE_LEVELS[item.minRole])
        .map(item => {
            if (item.type === 'divider') {
                return `<div class="nav-divider">${item.label}</div>`;
            }
            return `<div class="nav-item ${item.id === currentPage ? 'active' : ''}" onclick="navigate('${item.id}')">
                <span class="nav-icon">${item.icon}</span> ${item.label}
            </div>`;
        }).join('');
    
    document.getElementById('sidebar-user-name').textContent = 'บอส';
    const roleLabels = { owner: '👑 Owner', super_admin: '⚡ Super Admin', admin: '🛡️ Admin', moderator: '📋 Moderator' };
    document.getElementById('sidebar-user-role').textContent = roleLabels[admin.role] || admin.role;
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ========== NAVIGATION ==========
function navigate(page) {
    currentPage = page;
    try { window.__currentPage = page; } catch {}
    try { localStorage.setItem("dashLastPage", page); } catch {}
    renderSidebar();
    const titles = {
        dashboard: '📊 ภาพรวม', inbox: '📥 Inbox สลิป', customers: '👥 ลูกค้า', finance: '💰 การเงิน', receivers: '💳 บัญชีรับเงิน', gacha: '🎰 กาชา',
        promotions: '🎁 โปรโมชั่น + ตั้งค่าบอท', journey: '📨 DM อัตโนมัติ (Customer Journey)', content: '📸 Content', groups: '📱 กลุ่ม', bot_groups: '🤖 จัดการบอท', group_analytics: '📊 สถิติกลุ่ม', bot_schedules: '⏰ ตารางเวลาบอท', content_editor: '📝 คอนเทนต์บอท',
        team: '👨‍💼 ทีมงาน', settings: '⚙️ ตั้งค่า', marketing: '📊 Marketing',
        activity: '📋 Activity Log', health: '🚦 สถานะระบบ (System Health)', prae_logs: '💬 Prae Logs',
    };
    document.getElementById('page-title').textContent = titles[page] || page;
    document.getElementById('sidebar').classList.remove('open');
    
    // Destroy old charts
    Object.values(charts).forEach(c => c.destroy && c.destroy());
    charts = {};
    
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    
    const pages = {
        today: renderToday, dashboard: renderDashboard, inbox: renderInbox, customers: renderCustomers, finance: renderFinance, receivers: renderReceivers, gacha: renderGacha,
        promotions: renderPromoManager, journey: renderJourney, content: renderContent, groups: renderGroups, bot_groups: renderBotGroups, group_analytics: renderGroupAnalytics, bot_schedules: renderBotSchedules, content_editor: renderContentEditor,
        team: renderTeam, settings: renderSettings, marketing: renderMarketing,
        activity: renderActivityLog, health: renderSystemHealth, prae_logs: renderPraeLogs,
    };
    (pages[page] || (() => { content.innerHTML = '<div class="empty-state"><div class="icon">🚧</div><p>Coming soon</p></div>'; }))();
}

// Restore last-visited page on init
function restoreLastPage() {
    try {
        const last = localStorage.getItem("dashLastPage");
        if (last && typeof navigate === "function") {
            const valid = (typeof NAV_ITEMS !== "undefined") && NAV_ITEMS.some(n => n.id === last);
            if (valid) { navigate(last); return true; }
        }
    } catch (e) {}
    return false;
}

// ========== TOAST ==========
/**
 * Custom confirm modal — ใช้แทน window.confirm() ที่หน้าตาแย่
 * Usage: const ok = await confirmModal({title, message, okLabel, dangerous});
 */
function confirmModal({ title = 'ยืนยันการกระทำ', message = '', okLabel = 'ยืนยัน', cancelLabel = 'ยกเลิก', dangerous = false } = {}) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:99999;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);animation:fadein 0.15s';
        overlay.innerHTML = `
            <div class="card" style="max-width:440px;width:90%;padding:1.5rem;animation:slideup 0.2s">
                <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.8rem">
                    <span style="font-size:1.6rem">${dangerous ? '⚠️' : '❓'}</span>
                    <h3 style="margin:0;font-size:1.1rem">${title}</h3>
                </div>
                <div style="margin-bottom:1.2rem;white-space:pre-line;line-height:1.6;font-size:0.95rem;opacity:0.9">${message}</div>
                <div style="display:flex;gap:0.6rem;justify-content:flex-end">
                    <button class="btn btn-outline" id="_cm_cancel">${cancelLabel}</button>
                    <button class="btn ${dangerous ? 'btn-danger' : 'btn-primary'}" id="_cm_ok">${okLabel}</button>
                </div>
            </div>
            <style>
                @keyframes fadein { from { opacity: 0 } to { opacity: 1 } }
                @keyframes slideup { from { transform: translateY(20px); opacity: 0 } to { transform: translateY(0); opacity: 1 } }
                .btn-danger { background:#ef4444 !important; color:#fff !important; border-color:#ef4444 !important; }
                .btn-danger:hover { background:#dc2626 !important; }
            </style>
        `;
        document.body.appendChild(overlay);
        const cleanup = (val) => { overlay.remove(); resolve(val); };
        overlay.querySelector('#_cm_ok').onclick = () => cleanup(true);
        overlay.querySelector('#_cm_cancel').onclick = () => cleanup(false);
        overlay.onclick = (e) => { if (e.target === overlay) cleanup(false); };
        // Auto-focus OK button
        setTimeout(() => overlay.querySelector('#_cm_ok').focus(), 50);
    });
}


function toast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 5000);
}



// Universal authed download helper (Excel/CSV/etc)
async function downloadAuthed(url, filename) {
    try {
        const resp = await fetch('/api' + url, { headers: { 'Authorization': 'Bearer ' + token } });
        if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
        const blob = await resp.blob();
        const a = document.createElement('a');
        const objUrl = URL.createObjectURL(blob);
        a.href = objUrl; a.download = filename || 'export.xlsx';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
        toast('✅ ดาวน์โหลดเรียบร้อย', 'success');
    } catch (err) {
        toast('❌ ' + (err.message || 'download failed'), 'error');
    }
}

// ========== MODAL ==========
function openModal(title, bodyHtml, opts) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    document.getElementById('modal-overlay').classList.remove('hidden');
    // Custom width support
    const mc = document.querySelector('.modal-content');
    if (mc) {
        if (opts && opts.wide) {
            mc.style.maxWidth = (typeof opts.wide === 'string' ? opts.wide : 'min(95vw, 1100px)');
        } else {
            mc.style.maxWidth = '';  // reset to CSS default 560px
        }
    }
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
var dashboardPeriod = 'month';
var dashboardDateFrom = isoDate(new Date());
var dashboardDateTo = isoDate(new Date());
var dashboardMonth = isoMonth(new Date());

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







// ========== PAGE: RECEIVERS (bank accounts) ==========
async function renderReceivers() {
    const content = document.getElementById('page-content');
    try {
        const data = await api('/receivers');
        const items = data.items || [];

        const enabledCount = items.filter(r => r.enabled).length;
        const totalCumulative = items.reduce((acc, r) => acc + parseFloat(r.cumulative_received || 0), 0);

        function rowHtml(r) {
            const isWarning = parseFloat(r.cumulative_received || 0) >= parseFloat(r.alert_threshold || 0);
            const qrHtml = r.qr_url
                ? `<img src="${esc(r.qr_url)}" style="width:90px;height:90px;object-fit:contain;border:1px solid var(--border);border-radius:8px;background:#fff;cursor:pointer;" onclick="window.open('${esc(r.qr_url)}','_blank')" alt="QR">`
                : `<div style="width:90px;height:90px;border:1.5px dashed var(--border-strong);border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--text-dim);font-size:0.7rem;text-align:center;line-height:1.2;">ไม่มี<br>QR</div>`;
            return `
                <div style="background:var(--surface); border:1px solid var(--border); border-left:3px solid ${r.enabled ? 'var(--success)' : 'var(--text-dim)'}; border-radius:10px; padding:1.125rem 1.25rem; margin-bottom:0.875rem;">
                    <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; flex-wrap:wrap; margin-bottom:0.875rem;">
                        <div style="min-width:0; flex:1;">
                            <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem; flex-wrap:wrap;">
                                <h3 style="margin:0; font-size:1rem; font-weight:600; color:var(--text);">${esc(r.owner_name)}</h3>
                                ${r.enabled
                                    ? '<span class="status-badge status-active">✅ เปิด</span>'
                                    : '<span class="status-badge status-pending">⛔ ปิด</span>'}
                                ${isWarning ? '<span class="status-badge status-pending">⚠️ เกิน threshold</span>' : ''}
                            </div>
                            <div style="font-size:0.8125rem; color:var(--text-muted);">
                                ${esc(r.bank_name_th)} · ${esc(r.account_no)}
                                ${r.bank_last5 ? `· last5: <span style="font-family:var(--font-mono);">${esc(r.bank_last5)}</span>` : ''}
                            </div>
                            ${r.promptpay_number ? `<div style="font-size:0.75rem; color:var(--text-dim); margin-top:0.2rem;">PromptPay: ${esc(r.promptpay_number)}</div>` : ''}
                        </div>
                        <div style="flex-shrink:0;">${qrHtml}</div>
                    </div>

                    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:0.75rem; margin-bottom:0.875rem;">
                        <div style="background:var(--surface-2); border-radius:8px; padding:0.625rem 0.75rem;">
                            <div style="font-size:0.6875rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em;">ยอดสะสม</div>
                            <div style="font-size:1.125rem; font-weight:600; color:${isWarning ? 'var(--error)' : 'var(--text)'}; font-variant-numeric:tabular-nums;">${fmtBaht(r.cumulative_received)}</div>
                        </div>
                        <div style="background:var(--surface-2); border-radius:8px; padding:0.625rem 0.75rem;">
                            <div style="font-size:0.6875rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em;">Threshold</div>
                            <div style="font-size:1.125rem; font-weight:600; color:var(--text); font-variant-numeric:tabular-nums;">${fmtBaht(r.alert_threshold)}</div>
                        </div>
                        <div style="background:var(--surface-2); border-radius:8px; padding:0.625rem 0.75rem;">
                            <div style="font-size:0.6875rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.04em;">Weight</div>
                            <div style="font-size:1.125rem; font-weight:600; color:var(--text); font-variant-numeric:tabular-nums;">${r.weight}</div>
                        </div>
                    </div>

                    <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
                        <button class="btn btn-outline btn-sm" onclick="receiverReset(${r.id}, '${esc(r.owner_name).replace(/'/g, "\\'")}', ${parseFloat(r.cumulative_received || 0)})">🔄 Reset ยอดสะสม</button>
                        <button class="btn btn-outline btn-sm" onclick="receiverToggle(${r.id}, ${!r.enabled})">${r.enabled ? '⛔ ปิดบัญชี' : '✅ เปิดบัญชี'}</button>
                        <button class="btn btn-outline btn-sm" onclick="receiverEdit(${r.id}, ${r.weight}, ${parseFloat(r.alert_threshold || 0)})">⚙️ ตั้งค่า</button>
                        <button class="btn btn-outline btn-sm" onclick="receiverHistory(${r.id}, '${esc(r.owner_name).replace(/'/g, "\\'")}')">📜 ประวัติ</button>
                    </div>
                </div>
            `;
        }

        content.innerHTML = `
            <div style="max-width:880px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1.25rem; flex-wrap:wrap; gap:1rem;">
                    <div>
                        <h2 style="margin:0; font-size:1.25rem; font-weight:600; color:var(--text); letter-spacing:-0.02em;">💳 บัญชีรับเงิน</h2>
                        <p style="margin:0.25rem 0 0; color:var(--text-muted); font-size:0.875rem;">
                            ${enabledCount}/${items.length} บัญชีเปิดอยู่ · ยอดสะสมรวม ${fmtBaht(totalCumulative)}
                        </p>
                    </div>
                    <div style="display:flex; gap:0.5rem;">
                        <button class="btn btn-outline btn-sm" onclick="renderReceivers()">🔄 รีโหลด</button>
                        <button class="btn btn-primary btn-sm" onclick="receiverNew()">➕ เพิ่มบัญชี</button>
                    </div>
                </div>

                <div style="background:var(--surface-2); border:1px solid var(--border); border-radius:8px; padding:0.75rem 1rem; margin-bottom:1.25rem; font-size:0.8125rem; color:var(--text-muted);">
                    💡 <b>Reset ยอดสะสม</b> หลังถอนเงินออกจากบัญชี — ระบบจะส่ง alert ใหม่เมื่อถึง threshold
                </div>

                ${items.map(rowHtml).join('') || '<div class="empty-state"><div class="icon">💳</div><p>ยังไม่มีบัญชีรับเงิน</p></div>'}
            </div>
        `;
    } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function receiverReset(rid, owner, currentBaht) {
    if (!await confirmModal({ message: `Reset ยอดสะสมของ "${owner}"?\n\nยอดปัจจุบัน: ${fmtBaht(currentBaht)}\nจะกลับเป็น ฿0\n\nใช้หลังถอนเงินออกจากบัญชีแล้วเท่านั้น`, dangerous: true })) return;
    try {
        await api(`/receivers/${rid}/reset`, { method: 'POST' });
        toast(`✅ Reset ${owner} เรียบร้อย`, 'success');
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function receiverToggle(rid, newEnabled) {
    const action = newEnabled ? 'เปิด' : 'ปิด';
    if (!await confirmModal({ message: `${action}บัญชีนี้?`, dangerous: true })) return;
    try {
        await api(`/receivers/${rid}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled: newEnabled }),
        });
        toast(`✅ ${action}เรียบร้อย`, 'success');
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function receiverEdit(rid, currentWeight, currentThreshold) {
    openModal('⚙️ ตั้งค่าบัญชี #' + rid, `
        <div class="form-group">
            <label>Weight (น้ำหนัก rotation 0-100)</label>
            <input type="number" id="rcv-weight" min="0" max="100" value="${currentWeight}">
            <div style="font-size:0.75rem; color:var(--text-dim); margin-top:0.25rem;">บัญชีที่ weight สูง = รับเงินบ่อยกว่า</div>
        </div>
        <div class="form-group">
            <label>Alert threshold (บาท)</label>
            <input type="number" id="rcv-threshold" min="0" step="100" value="${currentThreshold}">
            <div style="font-size:0.75rem; color:var(--text-dim); margin-top:0.25rem;">เมื่อยอดสะสมถึง threshold ระบบส่ง alert ให้ถอน</div>
        </div>
        <button class="btn btn-primary btn-full" onclick="doReceiverEdit(${rid})">บันทึก</button>
    `);
}

async function doReceiverEdit(rid) {
    try {
        const weight = parseInt(document.getElementById('rcv-weight').value) || 0;
        const threshold = parseFloat(document.getElementById('rcv-threshold').value) || 0;
        await api(`/receivers/${rid}`, {
            method: 'PATCH',
            body: JSON.stringify({ weight, alert_threshold: threshold }),
        });
        toast('✅ บันทึกเรียบร้อย', 'success');
        closeModal();
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

// ===== Receivers: set cumulative manually + edit QR + create new =====
async function receiverSetCumulative(rid, owner, currentBaht) {
    openModal('✏️ แก้ตัวเลขสะสม', `
        <p style="font-size:0.875rem;color:var(--text-muted);margin-bottom:1rem;">
            แก้ตัวเลขยอดสะสมของ "${esc(owner)}" ให้ตรงกับยอดเงินในบัญชีจริง
        </p>
        <div class="form-group">
            <label>ยอดสะสมปัจจุบัน</label>
            <input type="text" value="${fmtBaht(currentBaht)}" disabled>
        </div>
        <div class="form-group">
            <label>ยอดสะสมใหม่ (บาท)</label>
            <input type="number" id="rcv-new-cumulative" min="0" step="1" placeholder="เช่น 4835">
        </div>
        <button class="btn btn-primary btn-full" onclick="doReceiverSetCumulative(${rid})">บันทึก</button>
    `);
}

async function doReceiverSetCumulative(rid) {
    try {
        const val = parseFloat(document.getElementById('rcv-new-cumulative').value);
        if (isNaN(val) || val < 0) { toast('กรอกตัวเลข ≥ 0', 'error'); return; }
        await api(`/receivers/${rid}`, {
            method: 'PATCH',
            body: JSON.stringify({ cumulative_received: val }),
        });
        toast('✅ ปรับยอดเรียบร้อย', 'success');
        closeModal();
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function receiverEditQr(rid, owner, currentUrl) {
    const preview = currentUrl
        ? `<img src="${esc(currentUrl)}" style="max-width:200px;max-height:200px;border:1px solid var(--border);border-radius:8px;background:#fff;display:block;margin:0 auto 0.875rem;">`
        : '<div style="text-align:center;color:var(--text-dim);font-size:0.875rem;margin-bottom:0.875rem;">ยังไม่มี QR</div>';
    openModal('📱 QR Code — ' + esc(owner), `
        ${preview}
        <div class="form-group">
            <label>อัปโหลดรูป QR ใหม่</label>
            <input type="file" id="rcv-qr-file" accept="image/png,image/jpeg,image/webp">
            <div style="font-size:0.75rem;color:var(--text-dim);margin-top:0.25rem;">รองรับ PNG / JPG / WEBP สูงสุด 4MB</div>
        </div>
        <div style="display:flex;gap:0.5rem;">
            <button class="btn btn-primary" style="flex:1;" onclick="doReceiverUploadQr(${rid})">📤 อัปโหลด</button>
            ${currentUrl ? `<button class="btn btn-outline" onclick="doReceiverRemoveQr(${rid})">🗑 ลบ QR</button>` : ''}
        </div>
    `);
}

async function doReceiverUploadQr(rid) {
    try {
        const file = document.getElementById('rcv-qr-file')?.files?.[0];
        if (!file) { toast('เลือกไฟล์ก่อน', 'error'); return; }
        if (file.size > 4 * 1024 * 1024) { toast('ไฟล์ใหญ่เกิน 4MB', 'error'); return; }

        const fd = new FormData();
        fd.append('file', file);

        const resp = await fetch('/api/receivers/upload-qr', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token },
            body: fd,
        });
        if (!resp.ok) { const e = await resp.json().catch(()=>({})); throw new Error(e.detail || 'upload failed'); }
        const data = await resp.json();
        const url = data.url;

        await api(`/receivers/${rid}`, {
            method: 'PATCH',
            body: JSON.stringify({ qr_url: url }),
        });
        toast('✅ อัปโหลด QR สำเร็จ', 'success');
        closeModal();
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function doReceiverRemoveQr(rid) {
    if (!await confirmModal({ message: 'ลบรูป QR ออกจากบัญชีนี้?', dangerous: true })) return;
    try {
        await api(`/receivers/${rid}`, {
            method: 'PATCH',
            body: JSON.stringify({ qr_url: '' }),
        });
        toast('✅ ลบ QR เรียบร้อย', 'success');
        closeModal();
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function receiverNew() {
    openModal('➕ เพิ่มบัญชีรับเงิน', `
        <div class="form-row">
            <div class="form-group">
                <label>ชื่อเจ้าของบัญชี *</label>
                <input id="rn-owner" placeholder="เช่น นายชาคริต กิ่งวงษา">
            </div>
            <div class="form-group">
                <label>คำสำคัญสำหรับ slip OCR *</label>
                <input id="rn-keyword" placeholder="เช่น ชาคริต">
                <div style="font-size:0.7rem;color:var(--text-dim);margin-top:0.2rem;">substring ของชื่อ จะใช้ match สลิป</div>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>ธนาคาร (ชื่อไทย) *</label>
                <select id="rn-bank-name">
                    <option value="ธนาคารไทยพาณิชย์">ไทยพาณิชย์ (SCB)</option>
                    <option value="ธนาคารกสิกรไทย">กสิกรไทย (KBANK)</option>
                    <option value="ธนาคารกรุงเทพ">กรุงเทพ (BBL)</option>
                    <option value="ธนาคารกรุงไทย">กรุงไทย (KTB)</option>
                    <option value="ธนาคารกรุงศรีอยุธยา">กรุงศรี (BAY)</option>
                    <option value="ธนาคารทหารไทยธนชาต">ทหารไทยธนชาต (TTB)</option>
                    <option value="ธนาคารออมสิน">ออมสิน (GSB)</option>
                    <option value="ธนาคารธนชาต">ธนชาต (TBANK)</option>
                </select>
            </div>
            <div class="form-group">
                <label>รหัสธนาคาร *</label>
                <select id="rn-bank-code">
                    <option value="SCB">SCB</option>
                    <option value="KBANK">KBANK</option>
                    <option value="BBL">BBL</option>
                    <option value="KTB">KTB</option>
                    <option value="BAY">BAY</option>
                    <option value="TTB">TTB</option>
                    <option value="GSB">GSB</option>
                    <option value="TBANK">TBANK</option>
                </select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>เลขบัญชี *</label>
                <input id="rn-account" placeholder="เช่น 4142039642">
            </div>
            <div class="form-group">
                <label>PromptPay (optional)</label>
                <input id="rn-promptpay" placeholder="เบอร์ หรือ เลขประจำตัว">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Weight (0-100)</label>
                <input type="number" id="rn-weight" min="0" max="100" value="1">
            </div>
            <div class="form-group">
                <label>Alert threshold (บาท)</label>
                <input type="number" id="rn-threshold" min="0" step="100" value="5000">
            </div>
        </div>
        <div class="form-group">
            <label>QR code (optional — อัปโหลดทีหลังก็ได้)</label>
            <input type="file" id="rn-qr-file" accept="image/png,image/jpeg,image/webp">
        </div>
        <button class="btn btn-primary btn-full" onclick="doReceiverNew()">✅ สร้างบัญชี</button>
    `);
}

async function doReceiverNew() {
    try {
        const owner = document.getElementById('rn-owner').value.trim();
        const keyword = document.getElementById('rn-keyword').value.trim();
        const bankName = document.getElementById('rn-bank-name').value;
        const bankCode = document.getElementById('rn-bank-code').value;
        const account = document.getElementById('rn-account').value.trim();
        const promptpay = document.getElementById('rn-promptpay').value.trim();
        const weight = parseInt(document.getElementById('rn-weight').value) || 1;
        const threshold = parseFloat(document.getElementById('rn-threshold').value) || 0;
        const qrFile = document.getElementById('rn-qr-file')?.files?.[0];

        if (!owner || !keyword || !bankCode || !account) {
            toast('กรอกฟิลด์ที่มี * ให้ครบ', 'error');
            return;
        }

        // Upload QR first if present
        let qr_url = null;
        if (qrFile) {
            if (qrFile.size > 4 * 1024 * 1024) { toast('QR ใหญ่เกิน 4MB', 'error'); return; }
            const fd = new FormData();
            fd.append('file', qrFile);
            const resp = await fetch('/api/receivers/upload-qr', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + token },
                body: fd,
            });
            if (!resp.ok) { const e = await resp.json().catch(()=>({})); throw new Error(e.detail || 'qr upload failed'); }
            const data = await resp.json();
            qr_url = data.url;
        }

        await api('/receivers', {
            method: 'POST',
            body: JSON.stringify({
                owner_name: owner,
                bank_code: bankCode,
                bank_name_th: bankName,
                account_no: account,
                name_keyword: keyword,
                promptpay_number: promptpay || null,
                qr_url: qr_url,
                weight, alert_threshold: threshold,
            }),
        });
        toast('✅ สร้างบัญชีเรียบร้อย', 'success');
        closeModal();
        renderReceivers();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function receiverHistory(rid, owner) {
    try {
        const data = await api(`/receivers/${rid}/sender-history?limit=20`);
        const items = data.items || [];
        let rows = items.map(p => `
            <tr>
                <td>${fmtDateTime(p.created_at)}</td>
                <td style="font-variant-numeric:tabular-nums;">${fmtBaht(p.amount)}</td>
                <td>${esc(p.sender_name || '-')}</td>
                <td><span style="font-family:var(--font-mono); font-size:0.75rem;">${esc(p.sender_bank_account || '-')}</span></td>
            </tr>
        `).join('') || '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">ยังไม่มี payment ที่โอนเข้าบัญชีนี้</td></tr>';
        openModal(`📜 ประวัติเข้าบัญชี — ${owner}`, `
            <div class="table-wrap">
                <table>
                    <thead><tr><th>วันที่</th><th>จำนวน</th><th>ผู้โอน</th><th>เลขบัญชี</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `);
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}







// ===== Settings tab: รายการแบน =====
let bannedSubTab = 'slips';
async function loadBannedSettings() {
    const area = document.getElementById('settings-area');
    if (!area) return;
    area.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    try {
        const summary = await api('/settings/banned/summary');
        area.innerHTML = `
            <div class="mini-cards" style="margin-bottom:1rem;">
                <div class="mini-card" onclick="bannedSubTab='slips';renderBannedTable()" style="cursor:pointer;">
                    <div class="mini-card-label">📄 สลิปแบน</div>
                    <div class="mini-card-value" style="color:var(--error);">${fmt(summary.slips)}</div>
                </div>
                <div class="mini-card" onclick="bannedSubTab='senders';renderBannedTable()" style="cursor:pointer;">
                    <div class="mini-card-label">👤 ชื่อผู้โอนแบน</div>
                    <div class="mini-card-value" style="color:var(--error);">${fmt(summary.senders)}</div>
                </div>
                <div class="mini-card" onclick="bannedSubTab='users';renderBannedTable()" style="cursor:pointer;">
                    <div class="mini-card-label">🚫 ลูกค้าถูกแบน</div>
                    <div class="mini-card-value" style="color:var(--error);">${fmt(summary.banned_users)}</div>
                </div>
                <div class="mini-card" onclick="bannedSubTab='blocked_bots';renderBannedTable()" style="cursor:pointer;">
                    <div class="mini-card-label">🤖 บล็อกบอท</div>
                    <div class="mini-card-value" style="color:var(--text-muted);">${fmt(summary.blocked_bots)}</div>
                </div>
            </div>

            <div class="filters" style="margin-bottom:1rem;">
                <button class="filter-btn ${bannedSubTab==='slips'?'active':''}" onclick="bannedSubTab='slips';renderBannedTable()">📄 สลิป (${summary.slips})</button>
                <button class="filter-btn ${bannedSubTab==='senders'?'active':''}" onclick="bannedSubTab='senders';renderBannedTable()">👤 ผู้โอน (${summary.senders})</button>
                <button class="filter-btn ${bannedSubTab==='users'?'active':''}" onclick="bannedSubTab='users';renderBannedTable()">🚫 ลูกค้า (${summary.banned_users})</button>
                <button class="filter-btn ${bannedSubTab==='blocked_bots'?'active':''}" onclick="bannedSubTab='blocked_bots';renderBannedTable()">🤖 บล็อกบอท (${summary.blocked_bots})</button>
            </div>

            <div id="banned-table-area"></div>
        `;
        renderBannedTable();
    } catch (err) {
        area.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function renderBannedTable() {
    const wrap = document.getElementById('banned-table-area');
    if (!wrap) return;
    // Update chip active classes to match current bannedSubTab
    document.querySelectorAll('#settings-area .filter-btn').forEach(btn => {
        const onclickStr = btn.getAttribute('onclick') || '';
        const isActive = onclickStr.includes("bannedSubTab='" + bannedSubTab + "'");
        btn.classList.toggle('active', isActive);
    });
    wrap.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const limit = 50;
        const endpoint = bannedSubTab === 'slips' ? '/settings/banned/slips'
                       : bannedSubTab === 'senders' ? '/settings/banned/senders'
                       : bannedSubTab === 'users' ? '/settings/banned/users'
                       : '/settings/banned/blocked-bots';
        const data = await api(`${endpoint}?limit=${limit}`);
        const items = data.items || [];
        const total = data.total || 0;

        if (items.length === 0) {
            wrap.innerHTML = '<div class="empty-state"><div class="icon">✨</div><p>ไม่มีรายการในหมวดนี้</p></div>';
            return;
        }

        let table = '';
        if (bannedSubTab === 'slips') {
            const rows = items.map(r => `
                <tr>
                    <td>${r.id}</td>
                    <td><code style="font-size:0.7rem;">${esc(r.slip_trans_ref || '-')}</code></td>
                    <td><code style="font-size:0.7rem;">${esc((r.slip_hash || '').slice(0,16))}${r.slip_hash && r.slip_hash.length > 16 ? '…' : ''}</code></td>
                    <td>${esc(r.source_first_name || '?')} <small style="color:var(--text-dim);">(tg:${r.source_telegram_id || '?'})</small></td>
                    <td>${esc(r.reason || '-')}</td>
                    <td>${fmtDateTime(r.created_at)}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="unbanSlip(${r.id})">🔓 ปลดแบน</button></td>
                </tr>
            `).join('');
            table = `
                <div class="table-wrap"><table>
                    <thead><tr><th>ID</th><th>Trans ref</th><th>Slip hash</th><th>ผู้ส่ง</th><th>เหตุผล</th><th>เมื่อ</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        } else if (bannedSubTab === 'senders') {
            const rows = items.map(r => `
                <tr>
                    <td>${r.id}</td>
                    <td><b>${esc(r.sender_name)}</b></td>
                    <td>${esc(r.source_first_name || '?')} <small style="color:var(--text-dim);">(tg:${r.source_telegram_id || '?'})</small></td>
                    <td>${esc(r.reason || '-')}</td>
                    <td>${fmtDateTime(r.created_at)}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="unbanSender(${r.id})">🔓 ปลดแบน</button></td>
                </tr>
            `).join('');
            table = `
                <div class="table-wrap"><table>
                    <thead><tr><th>ID</th><th>ชื่อผู้โอน</th><th>ผู้ส่งสลิป</th><th>เหตุผล</th><th>เมื่อ</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        } else if (bannedSubTab === 'users') {
            const rows = items.map(r => `
                <tr>
                    <td>${r.id}</td>
                    <td>${esc(r.first_name || '?')} ${esc(r.last_name || '')}</td>
                    <td><code>${r.telegram_id}</code></td>
                    <td>${esc(r.username || '-')}</td>
                    <td style="font-variant-numeric:tabular-nums;">${fmtBaht(r.total_spent)}</td>
                    <td>${esc(r.banned_reason || '-')}</td>
                    <td>${fmtDateTime(r.banned_at)}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="showCustomer360(${r.id})">👤 เปิดดู</button></td>
                </tr>
            `).join('');
            table = `
                <div class="table-wrap"><table>
                    <thead><tr><th>ID</th><th>ชื่อ</th><th>Telegram ID</th><th>Username</th><th>ยอดจ่าย</th><th>เหตุผล</th><th>เมื่อ</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        } else {
            // blocked_bots
            const rows = items.map(r => `
                <tr>
                    <td>${r.id}</td>
                    <td>${esc(r.first_name || '?')} ${esc(r.last_name || '')}</td>
                    <td><code>${r.telegram_id}</code></td>
                    <td>${esc(r.username || '-')}</td>
                    <td style="font-variant-numeric:tabular-nums;">${fmtBaht(r.total_spent)}</td>
                    <td>${fmtDateTime(r.blocked_bot_at)}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="showCustomer360(${r.id})">👤 เปิดดู</button></td>
                </tr>
            `).join('');
            table = `
                <div style="background:var(--surface-2);padding:0.75rem;border-radius:8px;margin-bottom:0.75rem;font-size:0.875rem;color:var(--text-muted);">
                    💡 ลูกค้าเหล่านี้ block sales bot — ระบบจะไม่ส่ง DM ใหม่ให้ ถ้าลูกค้า unblock บอท ระบบจะรีเซ็ตอัตโนมัติเมื่อทักครั้งต่อไป
                </div>
                <div class="table-wrap"><table>
                    <thead><tr><th>ID</th><th>ชื่อ</th><th>Telegram ID</th><th>Username</th><th>ยอดจ่าย</th><th>บล็อกเมื่อ</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        }
        wrap.innerHTML = `
            <div style="margin-bottom:0.5rem;font-size:0.8125rem;color:var(--text-muted);">แสดง ${items.length} จากทั้งหมด ${total}</div>
            ${table}
        `;
    } catch (err) {
        wrap.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function unbanSlip(id) {
    if (!await confirmModal({ message: 'ปลดแบนสลิปนี้? ลูกค้าจะส่งสลิปนี้ได้อีกครั้ง', dangerous: true })) return;
    try {
        await api(`/settings/banned/slips/${id}`, { method: 'DELETE' });
        toast('✅ ปลดแบนเรียบร้อย', 'success');
        loadBannedSettings();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function unbanSender(id) {
    if (!await confirmModal({ message: 'ปลดแบนชื่อผู้โอนนี้?', dangerous: true })) return;
    try {
        await api(`/settings/banned/senders/${id}`, { method: 'DELETE' });
        toast('✅ ปลดแบนเรียบร้อย', 'success');
        loadBannedSettings();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

// ===== Marketing link CRUD helpers =====
async function marketingLinkEditCost(linkId, currentCost, currentNotes) {
    openModal('💰 แก้ค่าโฆษณา — Link #' + linkId, `
        <div class="form-group">
            <label>ค่าโฆษณา (บาท)</label>
            <input type="number" id="mkt-cost" min="0" step="1" value="${currentCost || ''}" placeholder="เช่น 500">
        </div>
        <div class="form-group">
            <label>Notes (optional)</label>
            <input id="mkt-cost-notes" placeholder="เช่น: ครีเอเตอร์ A 2 คลิป" value="${esc(currentNotes || '')}">
        </div>
        <button class="btn btn-primary btn-full" onclick="doMarketingLinkCost(${linkId})">บันทึก</button>
    `);
}

async function doMarketingLinkCost(linkId) {
    try {
        const cost = parseFloat(document.getElementById('mkt-cost').value);
        const notes = document.getElementById('mkt-cost-notes').value.trim();
        if (isNaN(cost) || cost < 0) { toast('ใส่ตัวเลข ≥ 0', 'error'); return; }
        await api(`/marketing/links/${linkId}`, {
            method: 'PATCH',
            body: JSON.stringify({ cost, cost_notes: notes || null }),
        });
        toast('✅ บันทึกเรียบร้อย', 'success');
        closeModal();
        renderMarketing();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function marketingLinkRevoke(linkId, marketer, platform) {
    if (!await confirmModal({ message: 'Revoke link #' + linkId + ' (' + marketer + ' / ' + platform + ')?\n\nลิ้งนี้จะใช้ไม่ได้อีก:\n• Short URL จะ return 410\n• Group invite ถูก revoke ใน Telegram\n• Click logs จะไม่ track ใหม่', dangerous: true })) return;
    try {
        await api(`/marketing/links/${linkId}/revoke`, { method: 'POST' });
        toast('✅ Revoked เรียบร้อย', 'success');
        renderMarketing();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

// ========== PAGE: GACHA ADMIN ==========
let _gachaTab = 'overview';

async function renderGacha() {
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';

    try {
        const [overview, prizes, winners, recent] = await Promise.all([
            api('/gacha-admin/overview'),
            api('/gacha-admin/prizes'),
            api('/gacha-admin/top-winners?days=30&limit=10'),
            api('/gacha-admin/recent-pulls?limit=20'),
        ]);

        const td = overview.today, w7 = overview.last_7d, m30 = overview.last_30d;

        // Overview cards
        function card(label, value, sub, color) {
            return `
                <div style="background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:1.125rem 1.25rem; position:relative; overflow:hidden;">
                    <div style="position:absolute; top:0; left:0; right:0; height:2px; background:${color || 'var(--text-dim)'}; opacity:0.8;"></div>
                    <div style="font-size:0.6875rem; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:0.5rem;">${label}</div>
                    <div style="font-size:1.5rem; font-weight:600; color:var(--text); font-variant-numeric:tabular-nums; line-height:1.1;">${value}</div>
                    ${sub ? `<div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.35rem;">${sub}</div>` : ''}
                </div>`;
        }

        const overviewCards = `
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:0.875rem; margin-bottom:1.5rem;">
                ${card('🎰 หมุนวันนี้', `${td.pulls} ครั้ง`, `${td.users} คน · ฿${td.prize_value.toLocaleString()} prize`, 'var(--warning)')}
                ${card('💰 ฟรี vs จ่าย วันนี้', `${td.free_pulls} / ${td.paid_pulls}`, `ฟรี ${Math.round(td.free_pulls/Math.max(td.pulls,1)*100)}%`, 'var(--accent)')}
                ${card('📅 7 วัน', `${w7.pulls} pulls`, `${w7.users} unique · ฿${w7.prize_value.toLocaleString()} prize`, 'var(--success)')}
                ${card('📊 RTP 30 วัน', `${m30.rtp_pct}%`, `รายได้ ฿${m30.revenue.toLocaleString()} · จ่ายคืน ฿${m30.prize_value.toLocaleString()}`, 'var(--primary)')}
            </div>`;

        // Prize tables
        function legacyRow(p) {
            const pct = (parseFloat(p.probability) * 100).toFixed(2);
            return `
                <tr>
                    <td><b>${esc(p.label)}</b><br><small style="color:var(--text-dim); font-family:var(--font-mono);">${esc(p.code)}</small></td>
                    <td><span class="status-badge status-active">${esc(p.type)}</span></td>
                    <td style="font-variant-numeric:tabular-nums;">฿${parseFloat(p.value_thb || 0).toLocaleString()}</td>
                    <td style="font-variant-numeric:tabular-nums; font-weight:600;">${pct}%</td>
                    <td>${p.is_active ? '<span class="status-badge status-active">✅ เปิด</span>' : '<span class="status-badge status-rejected">⛔ ปิด</span>'}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="gachaToggleLegacy('${esc(p.code)}', ${!p.is_active})">${p.is_active ? '⛔ ปิด' : '✅ เปิด'}</button></td>
                </tr>`;
        }
        function poolRow(p) {
            return `
                <tr>
                    <td><b>${esc(p.name)}</b><br><small style="color:var(--text-dim); font-family:var(--font-mono);">${esc(p.code)}</small></td>
                    <td><span class="status-badge status-pending">${esc(p.tier)}</span></td>
                    <td>${esc(p.prize_type)}</td>
                    <td style="font-variant-numeric:tabular-nums;">฿${parseFloat(p.value_thb || 0).toLocaleString()}</td>
                    <td style="font-variant-numeric:tabular-nums; font-weight:600;">${parseFloat(p.probability_pct).toFixed(5)}%</td>
                    <td>${p.enabled ? '<span class="status-badge status-active">✅</span>' : '<span class="status-badge status-rejected">⛔</span>'}</td>
                    <td>
                        <div style="display:flex;gap:0.3rem;">
                            <button class="btn btn-sm btn-outline" onclick="gachaTogglePool(${p.id}, ${!p.enabled})">${p.enabled ? '⛔ ปิด' : '✅ เปิด'}</button>
                            <button class="btn btn-sm btn-danger" onclick="deletePrize(${p.id}, '${esc(p.name || '')}')" title="ลบรางวัล">🗑</button>
                        </div>
                    </td>
                </tr>`;
        }

        // Winners
        const winnersHtml = (winners.items || []).map(w => {
            const userLabel = w.username ? '@' + w.username : (w.first_name || 'Unknown');
            return `
                <tr>
                    <td>${fmtDateTime(w.pulled_at)}</td>
                    <td>${esc(userLabel)} <small style="color:var(--text-dim);">(${w.telegram_id})</small></td>
                    <td><b>${esc(w.prize_label)}</b></td>
                    <td style="font-variant-numeric:tabular-nums; font-weight:600; color:var(--success);">฿${parseFloat(w.prize_value_thb).toLocaleString()}</td>
                    <td>${w.payment_id ? '<span class="status-badge status-active">จ่าย</span>' : '<span class="status-badge status-pending">ฟรี</span>'}</td>
                </tr>`;
        }).join('') || '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">ยังไม่มี winner ใน 30 วัน</td></tr>';

        // Recent pulls
        const recentHtml = (recent.items || []).map(r => {
            const userLabel = r.username ? '@' + r.username : (r.first_name || 'User');
            return `
                <tr>
                    <td>${fmtDateTime(r.pulled_at)}</td>
                    <td>${esc(userLabel)}</td>
                    <td>${esc(r.prize_label || r.prize_code || '?')}</td>
                    <td style="font-variant-numeric:tabular-nums;">${r.prize_value_thb ? '฿' + parseFloat(r.prize_value_thb).toLocaleString() : '-'}</td>
                    <td>${r.payment_id ? '💰 จ่าย' : '🎁 ฟรี'}</td>
                </tr>`;
        }).join('') || '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">ยังไม่มีกิจกรรม</td></tr>';

        // Tab content
        function tabContent() {
            if (_gachaTab === 'overview') {
                return `
                    ${overviewCards}
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:1.25rem;">
                        <div>
                            <h3 style="margin:0 0 0.875rem; font-size:0.9375rem; font-weight:600;">🏆 Top Winners 30 วัน</h3>
                            <div class="table-wrap" style="max-height:520px; overflow:auto;">
                                <table>
                                    <thead><tr><th>เวลา</th><th>ผู้เล่น</th><th>รางวัล</th><th>ค่า</th><th>type</th></tr></thead>
                                    <tbody>${winnersHtml}</tbody>
                                </table>
                            </div>
                        </div>
                        <div>
                            <h3 style="margin:0 0 0.875rem; font-size:0.9375rem; font-weight:600;">📜 Recent Pulls (20 ล่าสุด)</h3>
                            <div class="table-wrap" style="max-height:520px; overflow:auto;">
                                <table>
                                    <thead><tr><th>เวลา</th><th>ผู้เล่น</th><th>รางวัล</th><th>ค่า</th><th>type</th></tr></thead>
                                    <tbody>${recentHtml}</tbody>
                                </table>
                            </div>
                        </div>
                    </div>`;
            } else if (_gachaTab === 'prizes_legacy') {
                const legacy = prizes.legacy_prizes || [];
                const totalPct = legacy.reduce((acc, p) => acc + parseFloat(p.probability) * 100, 0);
                return `
                    <div style="margin-bottom:1rem; padding:0.75rem 1rem; background:var(--surface-2); border-radius:8px; font-size:0.875rem; color:var(--text);">
                        💡 รวม probability: <b style="color:var(--primary);">${totalPct.toFixed(2)}%</b>
                        ${Math.abs(totalPct - 100) < 0.01 ? '✅' : '⚠️ ไม่เท่า 100% — ระบบจะ normalize เอง'}
                    </div>
                    <div class="table-wrap">
                        <table>
                            <thead><tr><th>รางวัล</th><th>Type</th><th>มูลค่า</th><th>โอกาส %</th><th>สถานะ</th><th></th></tr></thead>
                            <tbody>${legacy.map(legacyRow).join('')}</tbody>
                        </table>
                    </div>`;
            } else if (_gachaTab === 'prizes_pool') {
                const poolArr = prizes.prize_pool || [];
                const totalPct = poolArr.reduce((acc, p) => acc + parseFloat(p.probability_pct), 0);
                return `
                    <div style="margin-bottom:1rem; padding:0.75rem 1rem; background:var(--surface-2); border-radius:8px; font-size:0.875rem; color:var(--text);">
                        💡 รวม probability: <b style="color:var(--primary);">${totalPct.toFixed(5)}%</b>
                        ${Math.abs(totalPct - 100) < 0.01 ? '✅' : '⚠️ ไม่เท่า 100%'}
                    </div>
                    <div class="table-wrap">
                        <table>
                            <thead><tr><th>รางวัล</th><th>Tier</th><th>Type</th><th>มูลค่า</th><th>โอกาส %</th><th>สถานะ</th><th></th></tr></thead>
                            <tbody>${poolArr.map(poolRow).join('')}</tbody>
                        </table>
                    </div>`;
            }
            return '';
        }

        function tabBtn(key, label) {
            return `<button class="filter-btn ${_gachaTab===key?'active':''}" onclick="window.gachaTab('${key}')">${label}</button>`;
        }

        content.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1.25rem; flex-wrap:wrap; gap:1rem;">
                <div>
                    <h2 style="margin:0; font-size:1.25rem; font-weight:600; color:var(--text); letter-spacing:-0.02em;">🎰 กาชา</h2>
                    <p style="margin:0.25rem 0 0; color:var(--text-muted); font-size:0.875rem;">
                        ดูสถิติ + จัดการรางวัล + ติดตามกิจกรรม
                    </p>
                </div>
                <div style="display:flex;gap:0.4rem;">
                    <button class="btn btn-outline btn-sm" onclick="showGachaPricingModal()">💰 ราคา/หมุน</button>
                    <button class="btn btn-outline btn-sm" onclick="showAddPrizeModal()">🎁 เพิ่มรางวัล</button>
                    <button class="btn btn-outline btn-sm" onclick="renderGacha()">🔄 รีโหลด</button>
                </div>
            </div>

            <div class="filters" style="margin-bottom:1.25rem;">
                ${tabBtn('overview', '📊 ภาพรวม')}
                ${tabBtn('prizes_legacy', '🎁 รางวัลทั่วไป (' + (prizes.legacy_prizes?.length || 0) + ')')}
                ${tabBtn('prizes_pool', '💎 รางวัลใหญ่/Cash (' + (prizes.prize_pool?.length || 0) + ')')}
            </div>

            <div>${tabContent()}</div>
        `;

        window.gachaTab = (key) => {
            _gachaTab = key;
            renderGacha();
        };

    } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function gachaToggleLegacy(code, newActive) {
    if (!await confirmModal({ message: (newActive ? 'เปิด' : 'ปิด') + ' รางวัล ' + code + ' ?', dangerous: true })) return;
    try {
        await api(`/gacha-admin/prizes/legacy/${encodeURIComponent(code)}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_active: newActive }),
        });
        toast('✅ อัปเดตเรียบร้อย', 'success');
        renderGacha();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function gachaTogglePool(id, newEnabled) {
    if (!await confirmModal({ message: (newEnabled ? 'เปิด' : 'ปิด') + ' รางวัล id=' + id + ' ?', dangerous: true })) return;
    try {
        await api(`/gacha-admin/prize-pool/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled: newEnabled }),
        });
        toast('✅ อัปเดตเรียบร้อย', 'success');
        renderGacha();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

// ========== PAGE: CUSTOMER 360 (timeline view) ==========
const C360_TYPE_COLORS = {
    success: { bar: '#16a34a', dot: '#16a34a' },
    warning: { bar: '#d97706', dot: '#d97706' },
    error:   { bar: '#dc2626', dot: '#dc2626' },
    info:    { bar: '#0070f3', dot: '#0070f3' },
    default: { bar: '#9a9aa8', dot: '#9a9aa8' },
};

function _fmtEventDate(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        const opt = { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false };
        return d.toLocaleString('th-TH', opt);
    } catch { return iso; }
}

let _c360State = { userId: null, filter: 'all' };

async function showCustomer360(userId) {
    _c360State.userId = userId;
    _c360State.filter = 'all';
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';

    try {
        const [detail, payments, subs, groups, timeline] = await Promise.all([
            api(`/customers/${userId}`),
            api(`/customers/${userId}/payments`),
            api(`/customers/${userId}/subscriptions`),
            api(`/customers/${userId}/groups`),
            api(`/customers/${userId}/timeline`),
        ]);

        const u = detail.user;
        const activeSub = detail.subscription;
        const events = timeline.events || [];

        const username = u.username ? '@' + u.username : (u.first_name || 'User');
        const initial = (u.first_name || u.username || '?').charAt(0).toUpperCase();

        // Loyalty rank colors
        const rankBadges = {
            'NONE':     { label: 'NONE',    bg: '#f0f0f2', color: '#6e6e80' },
            'BRONZE':   { label: '🥉 Bronze',  bg: '#fef3e2', color: '#a85b00' },
            'SILVER':   { label: '🥈 Silver',  bg: '#eaeaea', color: '#525252' },
            'DIAMOND':  { label: '💎 Diamond', bg: '#dbeafe', color: '#1e3a8a' },
        };
        const rank = rankBadges[u.loyalty_rank || 'NONE'] || rankBadges.NONE;

        // Active subs from /subscriptions
        const activeSubs = (subs || []).filter(s => s.status === 'ACTIVE');
        const subsHtml = activeSubs.length === 0
            ? '<p style="color:var(--text-dim);font-size:0.8125rem;">ไม่มี active sub</p>'
            : activeSubs.map(s => `
                <div style="padding:0.625rem 0.75rem;background:var(--surface-2);border-radius:6px;margin-bottom:0.4rem;">
                    <div style="font-size:0.875rem;font-weight:600;color:var(--text);">${esc(s.package_name || '?')}</div>
                    <div style="font-size:0.75rem;color:var(--text-muted);margin-top:0.15rem;">ถึง ${fmtDate(s.end_date)}</div>
                </div>`).join('');

        // Groups
        const activeGroups = (groups || []).filter(g => g.status === 'ACTIVE' || g.status === undefined);
        const groupsHtml = activeGroups.length === 0
            ? '<p style="color:var(--text-dim);font-size:0.8125rem;">ไม่อยู่ในกลุ่ม</p>'
            : activeGroups.map(g => `<span class="status-badge status-active" style="margin:0.15rem;display:inline-block;">${esc(g.slug || '?')}</span>`).join('');

        // Marketing attribution from timeline (find first marketing_attribution event)
        const mktEvent = events.find(e => e.type === 'marketing_attribution');
        const mktHtml = mktEvent
            ? `<div style="font-size:0.8125rem;color:var(--text);font-weight:500;">${esc(mktEvent.title.replace('เข้าระบบผ่าน ', ''))}</div>
               <div style="font-size:0.75rem;color:var(--text-muted);margin-top:0.15rem;">${esc(mktEvent.subtitle || '')}</div>`
            : '<p style="color:var(--text-dim);font-size:0.8125rem;">ไม่ผ่านลิ้ง marketing (Direct)</p>';

        // Action buttons (reuse existing customerAction)
        const canActAdmin = hasRole('admin');
        const actionsHtml = canActAdmin ? `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-top:1rem;">
                <button class="btn btn-outline btn-sm" onclick="customerAction(${u.id},'dm')">📩 DM</button>
                <button class="btn btn-outline btn-sm" onclick="customerAction(${u.id},'extend')">⏰ ต่อเวลา</button>
                <button class="btn btn-outline btn-sm" onclick="customerAction(${u.id},'upgrade')">🆙 อัพเกรด</button>
                <button class="btn btn-outline btn-sm" onclick="customerCancelSub(${u.id})">⛔ ยกเลิก sub</button>
                <button class="btn btn-outline btn-sm" onclick="customerReactivateSub(${u.id})">♻️ Reactivate</button>
                <button class="btn btn-outline btn-sm" onclick="regenInviteLinks(${u.id})">🔄 ลิงก์ใหม่</button>
                <button class="btn btn-outline btn-sm" onclick="customerGiftSub(${u.id})">🎁 Gift sub</button>
                <button class="btn btn-outline btn-sm" onclick="customerAction(${u.id},'kick')">🔨 เตะ</button>
                <button class="btn btn-${u.is_banned ? 'success' : 'danger'} btn-sm" onclick="customerAction(${u.id},'ban')">${u.is_banned ? '🔓 ปลดแบน' : '🚫 แบน'}</button>
            </div>` : '';

        // Filter chips
        const counts = events.reduce((acc, e) => {
            const grp = e.type.startsWith('payment') ? 'payment'
                      : e.type.startsWith('subscription') ? 'sub'
                      : e.type === 'gacha_reward' ? 'gacha'
                      : e.type === 'sos_alert' ? 'sos'
                      : 'other';
            acc[grp] = (acc[grp] || 0) + 1;
            return acc;
        }, {});

        // Render timeline
        function renderTimeline() {
            const f = _c360State.filter;
            const filtered = events.filter(e => {
                if (f === 'all') return true;
                if (f === 'payment') return e.type.startsWith('payment');
                if (f === 'sub') return e.type.startsWith('subscription');
                if (f === 'gacha') return e.type === 'gacha_reward';
                if (f === 'sos') return e.type === 'sos_alert';
                if (f === 'other') return !e.type.startsWith('payment') && !e.type.startsWith('subscription') && e.type !== 'gacha_reward' && e.type !== 'sos_alert';
                return true;
            });

            if (filtered.length === 0) {
                return '<div class="empty-state" style="padding:2rem;"><div class="icon">📭</div><p>ไม่มีเหตุการณ์ในหมวดนี้</p></div>';
            }

            // Timeline rendering with vertical line
            let html = '<div style="position:relative;padding-left:1.75rem;">';
            html += '<div style="position:absolute;left:0.5rem;top:0.5rem;bottom:0.5rem;width:2px;background:var(--border);"></div>';
            filtered.forEach((e, i) => {
                const c = C360_TYPE_COLORS[e.color] || C360_TYPE_COLORS.default;
                html += `
                    <div style="position:relative;margin-bottom:0.875rem;">
                        <div style="position:absolute;left:-1.625rem;top:0.4rem;width:1rem;height:1rem;border-radius:50%;background:${c.dot};border:2px solid var(--surface);box-shadow:0 0 0 2px ${c.dot}33;display:flex;align-items:center;justify-content:center;font-size:0.6rem;">
                        </div>
                        <div style="background:var(--surface);border:1px solid var(--border);border-left:3px solid ${c.bar};border-radius:8px;padding:0.75rem 0.875rem;">
                            <div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.25rem;">
                                <span style="font-size:1rem;">${e.icon || '📝'}</span>
                                <span style="font-weight:600;font-size:0.875rem;color:var(--text);">${esc(e.title || '')}</span>
                            </div>
                            ${e.subtitle ? `<div style="font-size:0.8125rem;color:var(--text-muted);line-height:1.4;">${esc(e.subtitle)}</div>` : ''}
                            <div style="font-size:0.6875rem;color:var(--text-dim);margin-top:0.4rem;">${_fmtEventDate(e.at)}</div>
                        </div>
                    </div>`;
            });
            html += '</div>';
            return html;
        }

        function renderChips() {
            const chip = (key, label, count) => `
                <button class="filter-btn ${_c360State.filter===key?'active':''}" onclick="window.c360Filter('${key}')">
                    ${label}${count !== undefined ? ` <span style="opacity:0.7;font-size:0.7rem;">${count}</span>` : ''}
                </button>`;
            return chip('all', 'ทั้งหมด', events.length) +
                   chip('payment', '💰 จ่าย', counts.payment || 0) +
                   chip('sub', '📋 สมาชิก', counts.sub || 0) +
                   chip('gacha', '🎰 กาชา', counts.gacha || 0) +
                   chip('sos', '🆘 SOS', counts.sos || 0) +
                   chip('other', '📝 อื่น', counts.other || 0);
        }

        // 3-column layout. Mobile: collapses to single column via CSS.
        content.innerHTML = `
            <div style="display:flex;align-items:center;gap:0.625rem;margin-bottom:1.25rem;">
                <button class="btn btn-outline btn-sm" onclick="navigate('customers')">← กลับ</button>
                <h2 style="margin:0;font-size:1.1rem;font-weight:600;color:var(--text);">ข้อมูลลูกค้า</h2>
            </div>

            <div class="c360-grid" style="display:grid;grid-template-columns:280px 1fr 260px;gap:1.25rem;align-items:start;">

                <!-- LEFT: Identity card -->
                <div class="c360-left" style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.25rem;position:sticky;top:5rem;">
                    <div style="display:flex;align-items:center;gap:0.875rem;margin-bottom:1rem;">
                        <div style="width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,var(--primary),var(--primary-hover));color:#fff;display:flex;align-items:center;justify-content:center;font-size:1.5rem;font-weight:600;">${esc(initial)}</div>
                        <div style="min-width:0;">
                            <div style="font-weight:600;font-size:1rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;">${esc(u.first_name || '?')} ${esc(u.last_name || '')}</div>
                            <div style="font-size:0.8125rem;color:var(--text-muted);">${esc(username)}</div>
                        </div>
                    </div>

                    <div style="display:flex;flex-wrap:wrap;gap:0.4rem;margin-bottom:0.875rem;">
                        <span style="padding:0.2rem 0.6rem;border-radius:6px;background:${rank.bg};color:${rank.color};font-size:0.75rem;font-weight:600;">${rank.label}</span>
                        ${u.is_banned ? `<span class="status-badge status-rejected">🚫 BANNED</span>` : ''}
                        ${u.is_blocked_bot ? `<span class="status-badge status-pending">⚠️ Bot Blocked</span>` : ''}
                    </div>

                    <div style="font-size:0.75rem;color:var(--text-dim);margin-bottom:0.875rem;font-family:var(--font-mono);">tg: ${u.telegram_id}</div>

                    <div style="background:var(--surface-2);border-radius:8px;padding:0.75rem;margin-bottom:0.75rem;">
                        <div style="font-size:0.6875rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.25rem;">Total Spent</div>
                        <div style="font-size:1.375rem;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums;">${fmtBaht(u.total_spent)}</div>
                    </div>

                    <div style="font-size:0.75rem;color:var(--text-muted);">
                        <div>สมาชิกตั้งแต่: ${fmtDate(u.created_at)}</div>
                        ${u.phone ? `<div style="margin-top:0.15rem;">📞 ${esc(u.phone)}</div>` : ''}
                    </div>

                    ${actionsHtml}
                </div>

                <!-- CENTER: Timeline -->
                <div class="c360-center" style="min-width:0;">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.875rem;">
                        <h3 style="margin:0;font-size:0.9375rem;font-weight:600;color:var(--text);">📜 Timeline (${events.length} เหตุการณ์)</h3>
                        <button class="btn btn-outline btn-sm" onclick="showCustomer360(${u.id})">🔄</button>
                    </div>
                    <div class="filters" style="margin-bottom:0.875rem;">${renderChips()}</div>
                    <div id="c360-timeline">${renderTimeline()}</div>
                </div>

                <!-- RIGHT: Context cards -->
                <div class="c360-right" style="display:flex;flex-direction:column;gap:0.875rem;">
                    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1rem;">
                        <div style="font-size:0.6875rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.5rem;">📋 Active Subs</div>
                        ${subsHtml}
                    </div>
                    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1rem;">
                        <div style="font-size:0.6875rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.5rem;">🎯 Attribution</div>
                        ${mktHtml}
                    </div>
                    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1rem;">
                        <div style="font-size:0.6875rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.5rem;">🏠 กลุ่ม (${activeGroups.length})</div>
                        ${groupsHtml}
                    </div>
                    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1rem;">
                        <div style="font-size:0.6875rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.5rem;">📝 Notes ทีม</div>
                        <div id="notes-${u.id}"><div style="text-align:center;color:var(--text-dim);font-size:0.8rem;padding:0.5rem;">กำลังโหลด...</div></div>
                    </div>
                </div>

            </div>

            <style>
                @media (max-width: 980px) {
                    .c360-grid { grid-template-columns: 1fr !important; }
                    .c360-left { position: static !important; }
                }
            </style>
        `;

        // Phase A.2: load team notes
        try { renderCustomerNotes(u.id, "notes-" + u.id); } catch(_e) {}

        window.c360Filter = (key) => {
            _c360State.filter = key;
            const el = document.getElementById('c360-timeline');
            if (el) el.innerHTML = renderTimeline();
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            // re-mark active
            const active = Array.from(document.querySelectorAll('.filter-btn')).find(b => b.getAttribute('onclick')?.includes(`'${key}'`));
            if (active) active.classList.add('active');
        };

    } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

// ========== PAGE: INBOX (unified queue) ==========
async function renderInbox() {
    const content = document.getElementById('page-content');
    try {
        // Mock mode (preview only — no real data fetched)
        let data;
        if (window.__inboxMock === true) {
            data = {
                total: 5,
                counts: { payment: 2, sos: 2, broadcast: 1 },
                items: [
                    {
                        type: 'sos', id: 9001, severity: 'critical',
                        title: 'SOS — โน้ต',
                        subtitle: 'พี่ขอความช่วยเหลือหน่อยครับ จ่ายเงินแล้วแต่ยังไม่ได้ลิ้งห้อง',
                        telegram_id: 7630848719, username: 'notepad_th',
                        age_sec: 2100, status: 'PENDING'
                    },
                    {
                        type: 'payment', id: 871, severity: 'high',
                        title: 'Payment #871 — ฿890',
                        subtitle: 'Joffrey · GOD MODE 90 วัน',
                        telegram_id: 8502597269, username: 'joffrey_p',
                        amount: 890, age_sec: 1900, status: 'PENDING'
                    },
                    {
                        type: 'sos', id: 9002, severity: 'high',
                        title: 'SOS — Pim',
                        subtitle: 'เข้ากลุ่ม VIP ไม่ได้ค่ะ ลิ้งหมดอายุ',
                        telegram_id: 1234567890, username: 'pim_sjs',
                        age_sec: 1100, status: 'PENDING'
                    },
                    {
                        type: 'payment', id: 873, severity: 'medium',
                        title: 'Payment #873 — ฿500',
                        subtitle: 'Win · OnlyFans + VIP 30 วัน',
                        telegram_id: 5678901234, username: 'win_2024',
                        amount: 500, age_sec: 720, status: 'PENDING'
                    },
                    {
                        type: 'broadcast', id: 142, severity: 'low',
                        title: 'Broadcast — filter=active',
                        subtitle: 'พี่ๆ มีโปรแสงๆ จันทร์นี้นะคะ แพ็ก 300 ลด 25% แค่วันเดียว',
                        sent_by: 'boss', age_sec: 3700
                    },
                ]
            };
        } else {
            data = await api('/dashboard/inbox');
        }
        const total = data.total || 0;
        const c = data.counts || {payment:0, sos:0, broadcast:0};
        const items = data.items || [];

        const sevMeta = {
            critical: { color: '#dc2626', icon: '🚨' },
            high:     { color: '#dc2626', icon: '🔴' },
            medium:   { color: '#d97706', icon: '🟠' },
            normal:   { color: '#16a34a', icon: '🟡' },
            low:      { color: '#6e6e80', icon: '🟢' },
        };
        const typeMeta = {
            payment:   { icon: '💰', label: 'จ่ายเงิน' },
            sos:       { icon: '🆘', label: 'SOS' },
            broadcast: { icon: '📢', label: 'broadcast' },
        };

        function fmtAge(sec) {
            if (sec < 60) return sec + ' วิ';
            if (sec < 3600) return Math.floor(sec/60) + ' นาที';
            if (sec < 86400) return Math.floor(sec/3600) + ' ชม.';
            return Math.floor(sec/86400) + ' วัน';
        }

        let filter = 'all';

        function renderList() {
            const filtered = filter === 'all' ? items : items.filter(i => i.type === filter);
            if (filtered.length === 0) {
                return `<div class="empty-state" style="padding:3rem 1rem;">
                    <div class="icon" style="font-size:3.5rem;">✨</div>
                    <p style="font-size:1rem;font-weight:600;color:var(--text);margin-bottom:0.5rem;">ไม่มีงานค้าง</p>
                    <p style="color:var(--text-muted);font-size:0.875rem;">ทุกอย่างถูกจัดการเรียบร้อย</p>
                </div>`;
            }
            return filtered.map(it => {
                const sev = sevMeta[it.severity] || sevMeta.normal;
                const tm  = typeMeta[it.type] || { icon: '📋', label: it.type };
                let actions = '';
                if (it.type === 'payment') {
                    actions = `
                        <label style="display:inline-flex;align-items:center;gap:0.3rem;font-size:0.75rem;color:var(--text-muted);cursor:pointer;"><input type="checkbox" ${window._inboxSelected?.has(it.id) ? 'checked' : ''} onclick="event.stopPropagation();inboxToggle(${it.id})" style="width:auto;"> เลือก</label>
                        <button class="btn btn-sm btn-outline" onclick="event.stopPropagation(); openSlipImage(${it.id});">👁 ดูสลิป</button>
                        <button class="btn btn-sm btn-success" onclick="event.stopPropagation(); inboxAction('approve_payment', ${it.id});">✅ Approve</button>
                        <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); inboxAction('reject_payment', ${it.id});">❌ Reject</button>`;
                } else if (it.type === 'sos') {
                    actions = `
                        <button class="btn btn-sm btn-success" onclick="event.stopPropagation(); inboxAction('resolve_sos', ${it.id});">✅ จบ</button>
                        <button class="btn btn-sm btn-outline" onclick="event.stopPropagation(); window.open('tg://user?id=${it.telegram_id}', '_blank');">💬 ติดต่อ</button>`;
                } else if (it.type === 'broadcast') {
                    actions = `
                        <button class="btn btn-sm btn-outline" onclick="event.stopPropagation(); inboxAction('preview_broadcast', ${it.id});">👀 พรีวิว</button>`;
                }
                const username = it.username ? '@' + it.username : (it.telegram_id ? 'tg:' + it.telegram_id : '');
                const subtitle = esc(it.subtitle || '');
                return `
                <div class="inbox-row" style="
                    display:flex; gap:0.875rem; align-items:flex-start;
                    padding:1rem 1.125rem;
                    border:1px solid var(--border);
                    border-left:3px solid ${sev.color};
                    border-radius:var(--radius);
                    background:var(--surface);
                    margin-bottom:0.625rem;">
                    <div style="font-size:1.4rem; flex-shrink:0; line-height:1;">${sev.icon}</div>
                    <div style="flex:1; min-width:0;">
                        <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap; margin-bottom:0.25rem;">
                            <span style="font-size:0.6875rem; text-transform:uppercase; letter-spacing:0.04em;
                                  color:${sev.color}; font-weight:600;">${tm.icon} ${tm.label}</span>
                            <span style="font-size:0.75rem; color:var(--text-dim);">· ${fmtAge(it.age_sec || 0)} ที่แล้ว</span>
                        </div>
                        <div style="font-size:0.9375rem; font-weight:600; color:var(--text); margin-bottom:0.2rem;">
                            ${esc(it.title)}
                        </div>
                        ${subtitle ? `<div style="font-size:0.8125rem; color:var(--text-muted); margin-bottom:0.5rem;">${subtitle}</div>` : ''}
                        ${username ? `<div style="font-size:0.75rem; color:var(--text-dim); margin-bottom:0.5rem;">${esc(username)}</div>` : ''}
                        <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">${actions}</div>
                    </div>
                </div>`;
            }).join('');
        }

        function renderChips() {
            const chip = (key, label, count) => `
                <button class="filter-btn ${filter===key?'active':''}" onclick="window.inboxFilter('${key}')">
                    ${label} <span style="opacity:0.7;font-size:0.7rem;">${count}</span>
                </button>`;
            return chip('all', 'ทั้งหมด', total) +
                   chip('payment', '💰 จ่าย', c.payment || 0) +
                   chip('sos', '🆘 SOS', c.sos || 0) +
                   chip('broadcast', '📢 ส่ง', c.broadcast || 0);
        }

        function fullRender() {
            const isMock = window.__inboxMock === true;
            const mockBadge = isMock
                ? `<span style="background:rgba(247,176,69,0.15);color:#b8770b;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:0.5rem;">🎨 PREVIEW</span>`
                : '';
            const mockButton = (total === 0 && !isMock)
                ? `<button class="btn btn-outline btn-sm" onclick="window.__inboxMock=true; renderInbox();" style="margin-left:0.5rem;">🎨 ดูตัวอย่าง</button>`
                : (isMock
                    ? `<button class="btn btn-outline btn-sm" onclick="window.__inboxMock=false; renderInbox();" style="margin-left:0.5rem;">← กลับข้อมูลจริง</button>`
                    : '');
            content.innerHTML = `
                <div style="max-width:760px;">
                    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1.25rem;">
                        <div>
                            <h2 style="margin:0; font-size:1.25rem; font-weight:600; color:var(--text); letter-spacing:-0.02em;">📥 Inbox สลิป${mockBadge}</h2>
                            <p style="margin:0.25rem 0 0; color:var(--text-muted); font-size:0.875rem;">
                                ${total === 0 ? 'ไม่มีงานค้างค่ะ' : 'มี ' + total + ' งานรอจัดการ'}
                            </p>
                        </div>
                        <div>
                            <button class="btn btn-outline btn-sm" onclick="renderInbox()">🔄 รีโหลด</button>
                            ${mockButton}
                        </div>
                    </div>
                    <div class="filters" style="margin-bottom:1rem;">${renderChips()}</div>
                    <div id="inbox-list">${renderList()}</div>
                </div>`;
        }

        window.inboxFilter = (key) => {
            filter = key;
            const listEl = document.getElementById('inbox-list');
            if (listEl) listEl.innerHTML = renderList();
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        };

        fullRender();
    } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function inboxAction(action, id) {
    try {
        if (action === 'approve_payment') {
            if (!await confirmModal({ message: 'Approve payment #' + id + '?', dangerous: true })) return;
            await api(`/payments/${id}/approve`, { method: 'POST' });
            toast('✅ Approve เรียบร้อย', 'success');
            renderInbox();
        } else if (action === 'reject_payment') {
            await showRejectReasonModal(id);
            return;
        } else if (action === 'resolve_sos') {
            if (!await confirmModal({ message: 'จบ SOS ticket #' + id + '?', dangerous: true })) return;
            await api(`/dashboard/sos/${id}/resolve`, { method: 'POST' });
            toast('✅ Resolved', 'success');
            renderInbox();
        } else if (action === 'preview_broadcast') {
            alert('Preview broadcast — coming in Sprint 1.2');
        }
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
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
                    <div class="rev-sub"><span style="color:var(--text-muted);">${fmt(revSummary.today.count)} order</span>${revSummary.today.vs_yesterday_pct != null ? ` <span style="color:var(--text-muted);">·</span> <span style="color:${revSummary.today.vs_yesterday_pct >= 0 ? 'var(--success)' : 'var(--error)'};font-weight:600;">${revSummary.today.vs_yesterday_pct >= 0 ? '▲' : '▼'} ${Math.abs(revSummary.today.vs_yesterday_pct)}%</span>` : ''}</div>
                </div>
                <div class="rev-card month">
                    <div class="rev-label">📆 เดือนนี้</div>
                    <div class="rev-value">${fmtBaht(revSummary.this_month.amount)}</div>
                    <div class="rev-sub"><span style="color:var(--text-muted);">${fmt(revSummary.this_month.count)} order</span>${revSummary.this_month.vs_last_month_pct != null ? ` <span style="color:var(--text-muted);">·</span> <span style="color:${revSummary.this_month.vs_last_month_pct >= 0 ? 'var(--success)' : 'var(--error)'};font-weight:600;">${revSummary.this_month.vs_last_month_pct >= 0 ? '▲' : '▼'} ${Math.abs(revSummary.this_month.vs_last_month_pct)}%</span>` : ''}</div>
                </div>
                <div class="rev-card year">
                    <div class="rev-label">📊 ปีนี้</div>
                    <div class="rev-value">${fmtBaht(revSummary.this_year.amount)}</div>
                    <div class="rev-sub"><span style="color:var(--text-muted);">${fmt(revSummary.this_year.count)} order</span>${revSummary.this_year.vs_last_year_pct != null ? ` <span style="color:var(--text-muted);">·</span> <span style="color:${revSummary.this_year.vs_last_year_pct >= 0 ? 'var(--success)' : 'var(--error)'};font-weight:600;">${revSummary.this_year.vs_last_year_pct >= 0 ? '▲' : '▼'} ${Math.abs(revSummary.this_year.vs_last_year_pct)}%</span>` : ''}</div>
                </div>
                <div class="rev-card alltime">
                    <div class="rev-label">💎 รวมทั้งหมด</div>
                    <div class="rev-value">${fmtBaht(revSummary.all_time.amount)}</div>
                    <div class="rev-sub"><span style="color:var(--text-muted);">${fmt(revSummary.all_time.count)} order ตลอดอายุระบบ</span></div>
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
            <div class="detail-panel" style="margin-top:1rem;cursor:pointer;display:flex;justify-content:space-between;align-items:center;" onclick="navigate('inbox')">
                <div>
                    <div style="font-weight:600;color:var(--text);">📥 งานที่รออยู่ใน Inbox</div>
                    <small style="color:var(--text-muted);">สลิปรอตรวจ / SOS / Broadcasts</small>
                </div>
                <button class="btn btn-sm btn-primary">เปิด Inbox →</button>
            </div>`;
        
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
                            backgroundColor: chartAlpha(chartColors().primary, 0.6),
                            borderColor: chartColors().primary,
                            borderWidth: 1,
                            yAxisID: 'y',
                        },
                        {
                            type: 'line',
                            label: 'ลูกค้าที่ซื้อ (คน)',
                            data: analytics.chart.map(d => d.buyers),
                            borderColor: chartColors().success,
                            backgroundColor: chartAlpha(chartColors().success, 0.15),
                            tension: 0.35,
                            pointRadius: 3,
                            yAxisID: 'buyers',
                        }
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { ticks: { color: chartColors().text }, grid: { color: chartColors().grid } },
                        y: { position: 'left', ticks: { color: chartColors().text, callback: v => '฿' + fmt(v) }, grid: { color: chartColors().grid } },
                        buyers: { position: 'right', ticks: { color: chartColors().text, callback: v => fmt(v) }, grid: { drawOnChartArea: false } },
                    },
                    plugins: { legend: { labels: { color: chartColors().text } } },
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
    if (!await confirmModal({ message: `ส่งลิงก์เข้ากลุ่มใหม่ให้ลูกค้า ID: ${telegramId}?`, dangerous: true })) return;
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
    if (!await confirmModal({ message: `จบเคส SOS ของ ID ${telegramId}? (mark as resolved)`, dangerous: true })) return;
    try {
        await api(`/dashboard/sos/${telegramId}/resolve`, { method: 'POST' });
        toast('✅ จบเคสแล้ว', 'success');
        loadSOSAlerts();
        checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
}

async function batchResolveAllSOS() {
    if (!await confirmModal({ message: 'Resolve SOS ทั้งหมดที่ค้างอยู่?', dangerous: true })) return;
    try {
        const result = await api('/dashboard/sos/batch-resolve', { method: 'POST' });
        toast(`✅ Resolve สำเร็จ ${result.resolved_count} รายการ`, 'success');
        loadSOSAlerts();
        checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
}

var sosHistoryPage = 1, sosHistoryFilter = 'all';
async function showSOSHistory(page) {
    if (page) sosHistoryPage = page;
    try {
        const data = await api(`/dashboard/sos-history?status=${sosHistoryFilter}&page=${sosHistoryPage}&per_page=20`);
        let html = `<div class="filters" style="margin-bottom:1rem;">
            <button class="filter-btn ${sosHistoryFilter==='all'?'active':''}" onclick="sosHistoryFilter='all';sosHistoryPage=1;showSOSHistory()">ทั้งหมด</button>
            <button class="filter-btn ${sosHistoryFilter==='PENDING'?'active':''}" onclick="sosHistoryFilter='PENDING';sosHistoryPage=1;showSOSHistory()">รอตรวจ</button>
            <button class="filter-btn ${sosHistoryFilter==='RESOLVED'?'active':''}" onclick="sosHistoryFilter='RESOLVED';sosHistoryPage=1;showSOSHistory()">แก้ไขแล้ว</button>
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
    content.innerHTML = `

        <div class="filters">
            <input class="search-input" id="cust-search" placeholder="🔍 ค้นหา ชื่อ / Telegram ID / Username" value="${customerSearch}" onkeyup="if(event.key==='Enter'){customerSearch=this.value;customerPage=1;loadCustomers()}">
            <button class="filter-btn ${customerFilter==='all'?'active':''}" onclick="customerFilter='all';customerPage=1;loadCustomers()">ทั้งหมด</button>
            <button class="filter-btn ${customerFilter==='active'?'active':''}" onclick="customerFilter='active';customerPage=1;loadCustomers()">ใช้งานอยู่</button>
            <button class="filter-btn ${customerFilter==='expired'?'active':''}" onclick="customerFilter='expired';customerPage=1;loadCustomers()">หมดอายุ</button>
            <button class="filter-btn ${customerFilter==='banned'?'active':''}" onclick="customerFilter='banned';customerPage=1;loadCustomers()">ถูกแบน</button>
        </div>
        <div id="customers-table"><div class="loading"><div class="spinner"></div> กำลังโหลด...</div></div>
        <div id="customers-pagination"></div>
    `;
    loadCustomers();
}

// ========== BROADCAST ==========

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



let bcHistoryPage = 1;

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
    return showCustomer360(userId);
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
            if (await confirmModal({ message: 'ปลดแบนผู้ใช้นี้?', dangerous: true })) {
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
            <button class="filter-btn ${financeFilter==='all'?'active':''}" onclick="financeFilter='all';financePage=1;loadPayments()">ทั้งหมด</button>
            <button class="filter-btn ${financeFilter==='PENDING'?'active':''}" onclick="financeFilter='PENDING';financePage=1;loadPayments()">รอตรวจ</button>
            <button class="filter-btn ${financeFilter==='CONFIRMED'?'active':''}" onclick="financeFilter='CONFIRMED';financePage=1;loadPayments()">ยืนยันแล้ว</button>
            <button class="filter-btn ${financeFilter==='REJECTED'?'active':''}" onclick="financeFilter='REJECTED';financePage=1;loadPayments()">ปฏิเสธ</button>
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
    if (!await confirmModal({ message: 'อนุมัติสลิปนี้?', dangerous: true })) return;
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
        const colors = [chartColors().primary, '#00d2d3', '#feca57', '#ff6b6b', '#a29bfe', '#fd79a8'];
        
        if (byPkg.length) {
            charts.pkg = new Chart(document.getElementById('pkg-chart'), {
                type: 'doughnut',
                data: { labels: byPkg.map(r => r.name), datasets: [{ data: byPkg.map(r => parseFloat(r.total)), backgroundColor: colors }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: chartColors().text } } } }
            });
        }
        if (byMethod.length) {
            charts.method = new Chart(document.getElementById('method-chart'), {
                type: 'doughnut',
                data: { labels: byMethod.map(r => r.method), datasets: [{ data: byMethod.map(r => parseFloat(r.total)), backgroundColor: colors }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: chartColors().text } } } }
            });
        }
    } catch {}
}

// ========== PAGE: PROMOTIONS ==========
var promoTab = "comeback";
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
            <div class="tab ${promoTab==='bots'?'active':''}" onclick="promoTab='bots';renderPromotions()">🤖 บอท</div>
        </div>
        <div id="promo-content"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (promoTab === 'campaigns') loadPromotionCampaigns();
    else if (promoTab === 'performance') loadPromoPerformance();
    else if (promoTab === 'flash') loadFlashSales();
    else if (promoTab === 'code') loadPromoCodes();
    else if (promoTab === 'bots') loadBotsStatus();
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
    if (!await confirmModal({ message: 'ลบแคมเปญนี้?', dangerous: true })) return;
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
    if (!await confirmModal({ message: 'ลบ Flash Sale นี้?', dangerous: true })) return;
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
                <td>${c.is_active ? '<span style="color:var(--success)">ใช้งาน</span>' : '<span style="color:var(--text-dim)">ปิด</span>'}</td>
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
async function deletePromoCode(id) { if (!await confirmModal({ message: 'ลบ?', dangerous: true })) return; await api(`/promo-codes/${id}`, { method: 'DELETE' }); loadPromoCodes(); }

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

async function showScheduledForm() {
    let groupOpts = '<option value="G300">VIP ทั่วไป (G300)</option>';
    try {
        const groups = await api('/groups/categorized');
        const vip = groups.vip || groups.VIP || [];
        const free = groups.free || groups.FREE || [];
        const all = [...vip, ...free];
        if (all.length > 0) {
            groupOpts = all.map(g => `<option value="${esc(g.slug)}">${esc(g.title || g.slug)} (${esc(g.slug)})</option>`).join('');
        }
    } catch (err) {}

    openModal('📅 สร้างโปรโมทตั้งเวลา', `
        <div class="form-group"><label>ชื่อ</label><input id="sp-name" placeholder="ชื่อโปรโมชั่น"></div>
        <div class="form-group"><label>ข้อความ</label><textarea id="sp-msg" placeholder="ข้อความที่จะส่ง..."></textarea></div>
        <div class="form-group">
            <label>กลุ่มเป้าหมาย (กด Ctrl เพื่อเลือกหลายกลุ่ม)</label>
            <select id="sp-groups" multiple style="height:120px;">${groupOpts}</select>
        </div>
        <div class="form-row">
            <div class="form-group"><label>เวลา</label><input id="sp-time" type="datetime-local"></div>
            <div class="form-group"><label>ทุก</label><select id="sp-repeat"><option value="once">ครั้งเดียว</option><option value="daily">ทุกวัน</option><option value="weekly">ทุกสัปดาห์</option></select></div>
        </div>
        <button class="btn btn-primary btn-full" onclick="createScheduledPromo()">💾 บันทึก</button>
    `);
}

async function createScheduledPromo() {
    try {
        const sel = document.getElementById('sp-groups');
        const target_groups = Array.from(sel.selectedOptions).map(o => o.value);
        if (target_groups.length === 0) { toast('เลือกอย่างน้อย 1 กลุ่ม', 'error'); return; }
        const name = document.getElementById('sp-name').value.trim();
        const msg = document.getElementById('sp-msg').value.trim();
        const time = document.getElementById('sp-time').value;
        if (!name || !msg || !time) { toast('กรอกข้อมูลให้ครบ', 'error'); return; }
        await api('/scheduled-promotions', { method: 'POST', body: JSON.stringify({
            name, message_text: msg, scheduled_at: time,
            repeat_type: document.getElementById('sp-repeat').value,
            target_groups,
        })});
        toast(`✅ สร้างแล้ว — broadcast ${target_groups.length} กลุ่ม`, 'success');
        closeModal();
        loadScheduledPromos();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteScheduledPromo(id) {
    if (!await confirmModal({ message: 'ลบ?', dangerous: true })) return;
    await api(`/scheduled-promotions/${id}`, { method: 'DELETE' }); loadScheduledPromos();
}

// ========== PAGE: CONTENT ==========
var contentTab = 'queue';
async function renderContent() {
    const content = document.getElementById('page-content');
    content.innerHTML = `
        <div class="tabs">
            <div class="tab ${contentTab==='queue'?'active':''}" onclick="contentTab='queue';renderContent()">📦 Queue</div>
            
            <div class="tab ${contentTab==='stats'?'active':''}" onclick="contentTab='stats';renderContent()">📊 สถิติ</div>
        </div>
        <div id="content-area"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (contentTab === 'queue') loadContentQueue();
    
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
    if (!await confirmModal({ message: 'ลบ?', dangerous: true })) return;
    await api(`/content/queue/${id}`, { method: 'DELETE' }); loadContentQueue();
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

// ===== Audit 2026-06-25: Relay-bot sync helpers =====
async function renderRelaySync() {
    const el = document.getElementById('relay-sync-banner');
    if (!el) return;
    try {
        const r = await api('/groups/relay-sync-status');
        if (r.in_sync) {
            el.innerHTML = `
                <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.3);border-radius:8px;padding:0.625rem 0.875rem;display:flex;justify-content:space-between;align-items:center;font-size:0.875rem;">
                    <div>
                        <span style="font-weight:600;color:var(--success);">✅ Relay-bot sync แล้ว</span>
                        <span style="color:var(--text-muted);margin-left:0.5rem;">— ${r.db_count} กลุ่มฟรี broadcast ได้</span>
                    </div>
                    <button class="btn btn-sm btn-outline" onclick="syncRelayBot(true)" title="Force resync">🔄 Resync</button>
                </div>`;
        } else {
            const missing = r.missing_in_relay.map(g => `<code>${esc(g.slug)}</code>`).join(', ');
            const extra = r.extra_in_relay.length;
            el.innerHTML = `
                <div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.4);border-radius:8px;padding:0.75rem 1rem;font-size:0.875rem;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
                        <span style="font-weight:600;color:var(--warning);">⚠️ Relay-bot ไม่ sync กับ DB</span>
                        <button class="btn btn-sm btn-primary" onclick="syncRelayBot(false)">🔄 Sync ทันที</button>
                    </div>
                    <div style="font-size:0.78rem;color:var(--text-muted);line-height:1.6;">
                        DB: ${r.db_count} กลุ่ม · Relay-bot env: ${r.env_count} กลุ่ม<br>
                        ${r.missing_in_relay.length > 0 ? `<b>กลุ่มที่ relay ยังไม่รู้:</b> ${missing}<br>` : ''}
                        ${extra > 0 ? `<b>${extra} กลุ่มเก่าที่ไม่อยู่ใน DB แล้ว</b><br>` : ''}
                        <span style="color:var(--text-dim);">หมายเหตุ: relay-bot ใช้ env DEST_CHAT_IDS, ไม่อ่าน DB ตรง</span>
                    </div>
                </div>`;
        }
    } catch (err) {
        el.innerHTML = `<div style="color:var(--text-muted);font-size:0.78rem;">⚠️ เช็ค relay sync ไม่ได้: ${esc(err.message || '')}</div>`;
    }
}

async function syncRelayBot(force) {
    if (!force && !await confirmModal({ message: 'Sync กลุ่มทั้งหมดไปยัง relay-bot + restart container?\n\nrelay-bot จะ down ~15 วินาที — broadcast ระหว่างนั้นจะไม่ส่ง', dangerous: true })) return;
    try {
        toast('🔄 กำลัง sync + restart relay-bot...', 'info', 10000);
        const r = await api('/groups/relay-sync', { method: 'POST' });
        if (r.ok) {
            toast(`✅ Sync เรียบร้อย — ${r.synced_count} กลุ่ม`, 'success');
        } else {
            toast(`⚠️ Env เขียนแล้วแต่ restart ล้มเหลว: ${r.restart_error || 'unknown'}`, 'error', 10000);
        }
        renderRelaySync();
    } catch (err) {
        toast('❌ ' + (err.message || 'sync failed'), 'error');
    }
}

// ========== PAGE: GROUPS ==========
var _groupsTab = 'all';
async function renderGroups() {
    setTimeout(() => { try { renderRelaySync(); } catch (e) {} }, 200);

    const content = document.getElementById('page-content');
    try {
        const data = await api('/groups/categorized');

        // Tabs UI
        const tabs = [
            { id: 'all', label: '📑 ทั้งหมด' },
            { id: 'vip', label: '👑 VIP' },
            { id: 'free', label: '🆓 ฟรี' },
            { id: 'chat', label: '💬 พูดคุย' },
        ];

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
                    <td>${g.is_active ? '<span style="color:var(--success)">ใช้งาน</span>' : 'Off'}</td>
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
        
        // Build tab nav
        const tabHtml = tabs.map(t =>
            `<div class="tab ${_groupsTab===t.id?'active':''}" onclick="_groupsTab='${t.id}';renderGroups()">${t.label}</div>`
        ).join('');

        // Relay widget placeholder (loaded async)
        const relayWidget = '<div id="relay-widget" style="margin-bottom:1rem;"><div class="loading"><div class="spinner"></div></div></div>';

        let groupsHtml = '';
        if (_groupsTab === 'all' || _groupsTab === 'vip') groupsHtml += groupTable(data.vip, '👑', 'กลุ่ม VIP', 'vip');
        if (_groupsTab === 'all' || _groupsTab === 'free') groupsHtml += groupTable(data.free, '🆓', 'กลุ่มฟรี', 'free');
        if (_groupsTab === 'all' || _groupsTab === 'chat') groupsHtml += groupTable(data.chat, '💬', 'กลุ่มพูดคุย', 'chat');

        content.innerHTML = relayWidget + `<div class="tabs" style="margin-bottom:1rem;">${tabHtml}</div>` + groupsHtml;
        // Load relay widget now that DOM exists
        renderRelayWidget('relay-widget');
        
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
            slug: document.getElementById('grp-slug').value.replace(/\s+/g,'').toUpperCase(),
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
            <select id="egrp-active"><option value="true" ${isActive?'selected':''}>ใช้งาน</option><option value="false" ${!isActive?'selected':''}>ปิด</option></select>
        </div>
        <button class="btn btn-primary btn-full" onclick="updateGroup(${id})">💾 บันทึก</button>
    `);
}

async function updateGroup(id) {
    try {
        await api(`/groups/${id}`, { method: 'PATCH', body: JSON.stringify({
            title: document.getElementById('egrp-title').value,
            chat_id: parseInt(document.getElementById('egrp-chatid').value),
            min_tier: document.getElementById('egrp-tier').value,
            is_active: document.getElementById('egrp-active').value === 'true',
        })});
        toast('อัพเดตกลุ่มสำเร็จ', 'success'); closeModal(); renderGroups();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteGroup(id) {
    if (!await confirmModal({ message: 'ลบกลุ่มนี้?', dangerous: true })) return;
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

        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>Telegram ID</th><th>ยศ</th><th>สถานะ</th><th title="สิทธิ์ใช้บอตแต่ละตัว">🤖 บอท</th><th>Login ล่าสุด</th><th style="text-align:right;">จัดการ</th></tr></thead><tbody>';
        data.forEach(m => {
            const roleIcons = { owner: '👑', super_admin: '⚡', admin: '🛡️', moderator: '📋' };
            const roleIcon = roleIcons[m.role] || '📋';
            const myLevel = ROLE_LEVELS[admin.role] || 0;
            const theirLevel = ROLE_LEVELS[m.role] || 0;
            // canEdit: I outrank them AND they're not owner
            const canEdit = myLevel > theirLevel && m.role !== 'owner';
            const canDelete = hasRole('owner') && m.role !== 'owner' && m.id !== admin.id;
            const canReset = hasRole('owner') && m.id !== admin.id;
            const isMe = m.id === admin.id;

            const nameEsc = esc(m.display_name).replace(/'/g, '&#39;');
            const roleEsc = esc(m.role).replace(/'/g, '&#39;');

            let actions = `<button class="btn btn-sm btn-outline" onclick="showTeamActivity(${m.id})" title="ดูประวัติ">📋</button>`;
            if (canEdit) {
                actions = `<button class="btn btn-sm btn-outline" onclick="showEditTeam(${m.id},'${nameEsc}','${roleEsc}',${m.is_active})" title="แก้ไข">✏️</button>` + actions;
            }
            if (canReset) {
                actions += `<button class="btn btn-sm btn-outline" onclick="resetTeamPassword(${m.id},'${nameEsc}')" title="รีเซ็ตรหัสผ่าน">🔑</button>`;
            }
            if (canDelete) {
                actions += `<button class="btn btn-sm btn-danger" onclick="deleteTeamMember(${m.id},'${nameEsc}')" title="ลบทีมงาน">🗑</button>`;
            }
            if (isMe) {
                actions += ' <span style="font-size:0.7rem;color:var(--text-muted);margin-left:0.25rem;">(คุณ)</span>';
            }

            html += `<tr>
                <td>${esc(m.display_name)}</td>
                <td style="font-family:var(--font-mono);">${m.telegram_id}</td>
                <td>${roleIcon} ${esc(m.role)}</td>
                <td>${m.is_active ? '<span style="color:var(--success)">🟢 Active</span>' : '<span style="color:var(--error)">🔴 Disabled</span>'}</td>
                <td>${botPermsBadge(m.id, m.display_name, m.role, hasRole('super_admin') || hasRole('owner'))}</td>
                <td>${fmtDateTime(m.last_login_at)}</td>
                <td style="text-align:right;"><div class="btn-group" style="justify-content:flex-end;">${actions}</div></td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        content.innerHTML = html;
        // Async-load bot perm count badges
        setTimeout(() => { try { loadBotPermsBadges(); } catch (e) {} }, 100);
    } catch (e) { content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
}

async function deleteTeamMember(id, name) {
    if (!await confirmModal({ message: `ลบทีมงาน "${name}"?

• Account จะ disabled (soft delete)
• Session ทั้งหมดถูก revoke (logout ทันที)
• Activity history ยังคงอยู่ในระบบ

กลับคืนได้โดยให้ owner รีเปิด is_active`, dangerous: true })) return;
    try {
        await api(`/team/${id}`, { method: 'DELETE' });
        toast(`✅ ลบ ${name} เรียบร้อย`, 'success');
        renderTeam();
    } catch (e) {
        toast('❌ ' + (e.message || 'ลบไม่สำเร็จ'), 'error');
    }
}

async function resetTeamPassword(id, name) {
    const pw = prompt(`ตั้งรหัสผ่านใหม่ให้ "${name}" (อย่างน้อย 10 ตัวอักษร):`);
    if (!pw) return;
    if (pw.length < 10) { toast('รหัสผ่านต้อง ≥ 10 ตัวอักษร', 'error'); return; }
    try {
        await api(`/team/${id}/password-reset`, {
            method: 'POST',
            body: JSON.stringify({ new_password: pw }),
        });
        toast(`✅ Reset password ${name} เรียบร้อย (session ถูก revoke)`, 'success');
    } catch (e) {
        toast('❌ ' + (e.message || 'reset ไม่สำเร็จ'), 'error');
    }
}

async function showTeamActivity(id) {
    try {
        const r = await api(`/team/${id}/activity?limit=50`);
        const items = r.items || [];
        const body = items.length === 0
            ? '<div style="text-align:center;padding:1rem;color:var(--text-muted);">ยังไม่มี activity</div>'
            : items.map(it => `
                <div style="padding:0.5rem 0;border-top:1px solid var(--border);font-size:0.8rem;">
                    <div style="display:flex;justify-content:space-between;">
                        <span><b>${esc(it.action)}</b> ${it.entity_type ? `· ${esc(it.entity_type)} #${it.entity_id || '?'}` : ''}</span>
                        <span style="color:var(--text-muted);">${fmtDateTime(it.created_at)}</span>
                    </div>
                    ${it.details ? `<div style="color:var(--text-muted);font-size:0.72rem;margin-top:0.2rem;">${esc(JSON.stringify(it.details))}</div>` : ''}
                </div>
            `).join('');
        openModal(`📋 Activity — ${esc(r.member?.display_name || '')}`, `
            <div style="max-height:60vh;overflow:auto;">${body}</div>
            <div style="margin-top:0.75rem;font-size:0.75rem;color:var(--text-muted);text-align:center;">แสดง ${items.length} รายการล่าสุด</div>
        `);
    } catch (e) {
        toast('❌ ' + (e.message || 'load failed'), 'error');
    }
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
        <div class="form-group"><label>สถานะ</label><select id="et-active"><option value="true" ${isActive?'selected':''}>ใช้งาน</option><option value="false" ${!isActive?'selected':''}>ปิด</option></select></div>
        <div class="btn-group">
            <button class="btn btn-primary" onclick="updateTeam(${id})">💾 บันทึก</button>
            <button class="btn btn-outline" onclick="resetTeamPwd(${id})">🔑 Reset Password</button>
            <button class="btn btn-danger" onclick="deleteTeam(${id})">🗑 ลบ</button>
        </div>
    `);
}

async function updateTeam(id) {
    try {
        await api(`/team/${id}`, { method: 'PATCH', body: JSON.stringify({
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
        await api(`/team/${id}/password-reset`, { method: 'PATCH', body: JSON.stringify({ new_password: pw }), headers: { 'Content-Type': 'application/json' } });
        toast('Reset password แล้ว', 'success');
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteTeam(id) {
    if (!await confirmModal({ message: 'ลบทีมงานนี้?', dangerous: true })) return;
    try {
        await api(`/team/${id}`, { method: 'DELETE' });
        toast('ลบแล้ว', 'success'); closeModal(); renderTeam();
    } catch (e) { toast(e.message, 'error'); }
}


// ========== PAGE: SETTINGS ==========
var settingsTab = 'packages';
async function renderSettings() {
    const content = document.getElementById('page-content');
    content.innerHTML = `
        <div class="tabs">
            <div class="tab ${settingsTab==='packages'?'active':''}" onclick="settingsTab='packages';renderSettings()">📦 แพ็กเกจ</div>
            ${hasRole('owner') ? `` : ''}
            ${hasRole('super_admin') && !hasRole('owner') ? `` : ''}
            <div class="tab ${settingsTab==='banned'?'active':''}" onclick="settingsTab='banned';renderSettings()">🚫 รายการแบน</div>
            <div class="tab ${settingsTab==='prae_prompt'?'active':''}" onclick="settingsTab='prae_prompt';renderSettings()">🤖 บุคลิก Prae</div>
            <div class="tab ${settingsTab==='flags'?'active':''}" onclick="settingsTab='flags';renderSettings()">🚦 ฟีเจอร์ใหม่</div>
            <div class="tab ${settingsTab==='botmsg'?'active':''}" onclick="settingsTab='botmsg';renderSettings()">💬 คำพูดบอท</div>
            <div class="tab ${settingsTab==='config'?'active':''}" onclick="settingsTab='config';renderSettings()">⚙️ ค่าระบบ</div>
        </div>
        <div id="settings-area"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (settingsTab === 'packages') loadPackages();
    else if (settingsTab === 'config') loadSystemConfig();
    

    else if (settingsTab === 'banned') loadBannedSettings();
    else if (settingsTab === 'prae_prompt') loadPraePrompt();
    else if (settingsTab === 'flags') loadFeatureFlags();
    else if (settingsTab === 'botmsg') loadBotMessages();
}

async function loadPackages() {
    try {
        const data = await api('/settings/packages?show_all=true');
        // เก็บลง global เพื่อใช้ตอน edit
        window._packagesCache = data;
        let html = '';
        if (hasRole('owner')) html += `<button class="btn btn-primary" onclick="showPkgForm()" style="margin-bottom:1rem;">+ เพิ่มแพ็กเกจ</button>`;
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>Tier</th><th>ราคา</th><th>วัน</th><th>ห้อง</th><th>Active subs</th><th>สถานะ</th><th></th></tr></thead><tbody>';
        data.forEach(p => {
            let groups = '';
            try {
                const arr = typeof p.groups_access === 'string' ? JSON.parse(p.groups_access || '[]') : (p.groups_access || []);
                groups = Array.isArray(arr) ? arr.join(', ') : '—';
            } catch(e) { groups = '—'; }
            const dur = p.duration_days >= 36500 ? '∞ ถาวร' : (p.duration_days === 0 ? '—' : p.duration_days + ' วัน');
            const activeBadge = p.is_active ? '<span style="color:var(--success)">✓ เปิด</span>' : '<span style="color:#888">✕ ปิด</span>';
            const subsBadge = p.active_subs_count > 0 ? `<span style="color:var(--info);font-weight:600">${p.active_subs_count}</span>` : '<span style="color:#888">0</span>';
            const actions = hasRole('owner') ? `
                <button class="btn btn-sm btn-outline" onclick="editPkg(${p.id})" title="แก้ไข">✏️</button>
                <button class="btn btn-sm" style="color:#ef4444" onclick="deletePkg(${p.id}, '${esc(p.name).replace(/'/g, "&#39;")}', ${p.active_subs_count})" title="ลบ">🗑️</button>
            ` : '';
            html += `<tr>
                <td><b>${esc(p.name)}</b></td>
                <td><code>${esc(p.tier)}</code></td>
                <td>${fmtBaht(p.price)}</td>
                <td>${dur}</td>
                <td style="font-size:0.85rem">${esc(groups) || '—'}</td>
                <td style="text-align:center">${subsBadge}</td>
                <td>${activeBadge}</td>
                <td>${actions}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        html += '<div style="margin-top:1rem;opacity:.7;font-size:0.85rem">💡 Active subs = ลูกค้าที่กำลังใช้แพ็คเกจนี้อยู่ (ลบไม่ได้ถ้ามีคนใช้)</div>';
        document.getElementById('settings-area').innerHTML = html;
    } catch (e) { toast(e.message, 'error'); }
}

// === Helper: form fields ทั้งหมด ===
const _PKG_TIERS = ['TIER_99', 'TIER_100', 'TIER_300', 'TIER_500', 'TIER_1299', 'TIER_2499', 'TIER_ADD500', 'GACHA_1', 'GACHA_3', 'GACHA_10'];
const _AVAILABLE_GROUPS = ['G300', 'G500', 'SSS', 'VGOD', 'INTER', 'SERIES', 'RANDOM', 'SHAKER', 'SUMMER'];

function _pkgFormHtml(pkg) {
    const p = pkg || { name: '', tier: 'TIER_300', price: 0, duration_days: 30, description: '', groups_access: [], is_active: true, sort_order: 5 };
    const groupsArr = (() => {
        try { return typeof p.groups_access === 'string' ? JSON.parse(p.groups_access || '[]') : (p.groups_access || []); } catch(e) { return []; }
    })();
    const tierOpts = _PKG_TIERS.map(t => `<option value="${t}" ${p.tier === t ? 'selected' : ''}>${t}</option>`).join('');
    const groupChecks = _AVAILABLE_GROUPS.map(g => `
        <label style="display:inline-flex;align-items:center;margin:0.3rem 0.6rem 0.3rem 0;font-size:0.9rem">
            <input type="checkbox" class="pkg-group-chk" value="${g}" ${groupsArr.includes(g) ? 'checked' : ''} style="margin-right:0.3rem">
            ${g}
        </label>
    `).join('');
    return `
        <div class="form-group"><label>ชื่อแพ็คเกจ</label><input id="pkg-name" value="${esc(p.name || '')}" placeholder="VIP 30 วัน"></div>
        <div class="form-row">
            <div class="form-group"><label>Tier (รหัส)</label><select id="pkg-tier">${tierOpts}</select></div>
            <div class="form-group"><label>ราคา (฿)</label><input id="pkg-price" type="number" min="0" step="1" value="${p.price || 0}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>จำนวนวัน (0 = กาชา, 36500 = ถาวร)</label><input id="pkg-days" type="number" min="0" value="${p.duration_days || 0}"></div>
            <div class="form-group"><label>ลำดับแสดง (สูง = บน)</label><input id="pkg-sort" type="number" value="${p.sort_order || 5}"></div>
        </div>
        <div class="form-group"><label>รายละเอียด (optional)</label><input id="pkg-desc" value="${esc(p.description || '')}" placeholder="สั้นๆ สำหรับลูกค้า"></div>
        <div class="form-group">
            <label>ห้องที่ลูกค้าเข้าได้</label>
            <div style="margin-top:0.4rem;padding:0.7rem;background:rgba(255,255,255,0.05);border-radius:0.5rem">${groupChecks}</div>
        </div>
        <div class="form-group">
            <label style="display:flex;align-items:center"><input type="checkbox" id="pkg-active" ${p.is_active ? 'checked' : ''} style="margin-right:0.5rem"> เปิดใช้งาน (ลูกค้าซื้อได้)</label>
        </div>
    `;
}

function _collectPkgForm() {
    const groups = Array.from(document.querySelectorAll('.pkg-group-chk:checked')).map(c => c.value);
    return {
        name: document.getElementById('pkg-name').value.trim(),
        tier: document.getElementById('pkg-tier').value,
        price: parseFloat(document.getElementById('pkg-price').value) || 0,
        duration_days: parseInt(document.getElementById('pkg-days').value) || 0,
        description: document.getElementById('pkg-desc').value.trim() || null,
        groups_access: JSON.stringify(groups),
        is_active: document.getElementById('pkg-active').checked,
        sort_order: parseInt(document.getElementById('pkg-sort').value) || 5,
    };
}

function showPkgForm() {
    openModal('+ เพิ่มแพ็คเกจ', _pkgFormHtml(null) + `
        <button class="btn btn-primary btn-full" onclick="createPkg()" style="margin-top:1rem">💾 สร้าง</button>
    `);
}

async function createPkg() {
    const data = _collectPkgForm();
    if (!data.name) { toast('กรุณาใส่ชื่อ', 'error'); return; }
    try {
        await api('/settings/packages', { method: 'POST', body: JSON.stringify(data) });
        toast('สร้างแล้ว ✅', 'success'); closeModal(); loadPackages();
    } catch (e) { toast(e.message, 'error'); }
}

function editPkg(id) {
    const pkg = (window._packagesCache || []).find(p => p.id === id);
    if (!pkg) { toast('ไม่พบแพ็คเกจ', 'error'); return; }
    openModal('✏️ แก้ไข ' + pkg.name, _pkgFormHtml(pkg) + `
        <button class="btn btn-primary btn-full" onclick="updatePkg(${id})" style="margin-top:1rem">💾 บันทึก</button>
    `);
}

async function updatePkg(id) {
    const data = _collectPkgForm();
    if (!data.name) { toast('กรุณาใส่ชื่อ', 'error'); return; }
    try {
        await api(`/settings/packages/${id}`, { method: 'PATCH', body: JSON.stringify(data) });
        toast('อัพเดตแล้ว ✅', 'success'); closeModal(); loadPackages();
    } catch (e) { toast(e.message, 'error'); }
}

async function deletePkg(id, name, activeSubs) {
    if (activeSubs > 0) {
        toast(`ลบไม่ได้ — มีลูกค้า ${activeSubs} คนใช้แพ็คเกจนี้อยู่`, 'error');
        return;
    }
    if (!await confirmModal({ message: `ลบแพ็คเกจ "${name}"?\n(จะ soft-delete = is_active=false ไม่ใช่ลบจริง)`, dangerous: true })) return;
    try {
        await api(`/settings/packages/${id}`, { method: 'DELETE' });
        toast('ลบแล้ว ✅', 'success'); loadPackages();
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




async function testDM(type) {
    if (!await confirmModal({ message: `ทดสอบส่ง ${type} DM ให้ 1 คน?`, dangerous: true })) return;
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
                <div class="mini-card"><div class="mini-card-label">ใช้งาน</div><div class="mini-card-value">${fmt(kpi.active_members)}</div></div>
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

                        ` : ''}

            ${links.length > 0 ? `
            <div class="card card-full" style="margin-top:1rem;">
                <div class="card-label">🔗 ลิ้งทั้งหมด (${links.length})</div>
                <div style="overflow-x:auto;max-height:400px;overflow-y:auto;">
                <table style="width:100%;border-collapse:collapse;color:var(--text);font-size:0.8rem;">
                    <thead style="background:var(--surface-2);position:sticky;top:0;">
                        <tr>
                            <th style="padding:0.5rem;text-align:left;">ID</th>
                            <th style="padding:0.5rem;text-align:left;">Marketer</th>
                            <th style="padding:0.5rem;text-align:left;">Platform</th>
                            <th style="padding:0.5rem;text-align:left;">Short URL</th>
                            <th style="padding:0.5rem;text-align:right;">Cost</th>
                            <th style="padding:0.5rem;text-align:right;">Clicks</th>
                            <th style="padding:0.5rem;text-align:right;">Joins</th>
                            <th style="padding:0.5rem;text-align:right;">Revenue</th>
                            <th style="padding:0.5rem;text-align:right;">Profit</th>
                            <th style="padding:0.5rem;text-align:center;">Status</th>
                            <th style="padding:0.5rem;text-align:center;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${links.map(l => `
                        <tr style="border-top:1px solid var(--border);${l.is_revoked ? 'opacity:0.5;' : ''}">
                            <td style="padding:0.5rem;font-family:var(--font-mono,monospace);">#${l.id}</td>
                            <td style="padding:0.5rem;">${esc(l.marketer)}</td>
                            <td style="padding:0.5rem;">${esc(l.platform)}</td>
                            <td style="padding:0.5rem;">${l.short_url
                                ? `<a href="${esc(l.short_url)}" target="_blank" style="color:var(--accent);font-family:var(--font-mono);font-size:0.78rem;">${esc(l.short_code)}</a>`
                                : '<span style="color:var(--text-dim);font-size:0.78rem;">—</span>'}</td>
                            <td style="padding:0.5rem;text-align:right;">${l.cost > 0 ? fmtBaht(l.cost) : '<span style="color:var(--text-muted);">-</span>'}</td>
                            <td style="padding:0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(l.clicks || 0)}</td>
                            <td style="padding:0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(l.joins)}</td>
                            <td style="padding:0.5rem;text-align:right;color:var(--primary);font-variant-numeric:tabular-nums;">${fmtBaht(l.revenue)}</td>
                            <td style="padding:0.5rem;text-align:right;color:${l.profit >= 0 ? 'var(--success)' : 'var(--error)'};font-variant-numeric:tabular-nums;">${fmtBaht(l.profit)}</td>
                            <td style="padding:0.5rem;text-align:center;">${l.is_revoked ? '🔴 revoked' : '🟢 active'}</td>
                            <td style="padding:0.5rem;text-align:center;white-space:nowrap;">
                                <button class="btn btn-sm btn-outline" onclick="marketingLinkEditCost(${l.id}, ${parseFloat(l.cost || 0)}, '${esc(l.cost_notes || '').replace(/'/g, "\'")}')" title="แก้ Cost" style="padding:0.2rem 0.4rem;">💰</button>
                                ${l.is_revoked ? '' : `<button class="btn btn-sm btn-outline" onclick="marketingLinkRevoke(${l.id}, '${esc(l.marketer)}', '${esc(l.platform)}')" title="Revoke" style="padding:0.2rem 0.4rem;">🚫</button>`}
                            </td>
                        </tr>
                        `).join('')}
                    </tbody>
                </table>
                </div>
                <div style="font-size:0.75rem;color:var(--text-muted);padding:0.5rem 0;margin-top:0.5rem;">
                    💡 <b>💰</b> = แก้ค่าโฆษณา/notes &nbsp;·&nbsp; <b>🚫</b> = revoke (link จะใช้ไม่ได้). หรือผ่าน Discord: <code>cost &lt;id&gt; &lt;amount&gt;</code> ใน #ivy / #wasu / #pai
                </div>
            </div>
            ` : ''}

            <div id="mkt-heatmap" style="margin-top:1.5rem;"></div>

            <div class="card card-full" style="margin-top:1.5rem;">
                <div class="card-label">🤖 AI Action Items</div>
                <div style="padding:1rem;white-space:pre-wrap;color:var(--text);font-size:0.9rem;">${insights.insights || 'ยังไม่มี'}</div>
                ${insights.date ? `<div style="font-size:0.75rem;color:var(--text-dim);padding:0 1rem 1rem;">อัพเดต: ${insights.date}</div>` : ''}
            </div>
        `;

        // Weekly chart
        // Render heatmap (async; non-blocking)
        renderMarketingHeatmap('mkt-heatmap', 30);

        if (weekly.length >= 1) {
            charts.weekly = new Chart(document.getElementById('weekly-chart'), {
                type: 'bar',
                data: {
                    labels: weekly.map(w => w.week),
                    datasets: [{
                        label: 'รายได้ (฿)',
                        data: weekly.map(w => w.revenue),
                        backgroundColor: chartAlpha(chartColors().primary, 0.6),
                        borderColor: chartColors().primary,
                        borderWidth: 1,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { ticks: { color: chartColors().text }, grid: { color: chartColors().grid } },
                        y: { ticks: { color: chartColors().text, callback: v => '฿' + fmt(v) }, grid: { color: chartColors().grid } },
                    },
                    plugins: { legend: { labels: { color: chartColors().text } } },
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
            // Phase A.2 fix: pretty details + dont duplicate IP
            let detailsHtml = '-';
            if (a.details) {
                try {
                    const d = typeof a.details === 'string' ? JSON.parse(a.details) : a.details;
                    // Skip IP from details (shown in its own column)
                    const filtered = {...d};
                    delete filtered.ip; delete filtered.ip_address;
                    const keys = Object.keys(filtered);
                    if (keys.length === 0) {
                        detailsHtml = '<span style="color:var(--text-dim);">-</span>';
                    } else {
                        const preview = keys.slice(0, 2).map(k => `${esc(k)}=${esc(String(filtered[k]).slice(0,30))}`).join(', ');
                        const full = JSON.stringify(filtered, null, 2);
                        detailsHtml = `<details style="cursor:pointer;"><summary style="font-size:0.75rem;color:var(--text-muted);">${esc(preview)}${keys.length>2 ? ' …' : ''}</summary><pre style="font-family:var(--font-mono);font-size:0.7rem;background:var(--surface-2);padding:0.4rem;border-radius:4px;margin-top:0.3rem;white-space:pre-wrap;max-width:300px;">${esc(full)}</pre></details>`;
                    }
                } catch (_e) {
                    detailsHtml = `<span style="font-size:0.75rem;color:var(--text-muted);">${esc(String(a.details).slice(0, 80))}</span>`;
                }
            }
            html += `<tr>
                <td style="white-space:nowrap;">${fmtDateTime(a.created_at)}</td>
                <td>${esc(a.admin_name || a.admin_id)}</td>
                <td><span class="status-badge">${esc(a.action)}</span></td>
                <td>${esc(a.entity_type || '-')}</td>
                <td>${a.entity_id || '-'}</td>
                <td style="max-width:300px;">${detailsHtml}</td>
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

// Universal Export modal
function openExportsModal() {
    openModal('📥 Export Excel', `
        <div style="display:grid;gap:0.625rem;">
            <div class="detail-panel">
                <div style="font-weight:600;margin-bottom:0.5rem;color:var(--text);">💰 Payments</div>
                <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/payments?days=7', 'payments_7d.xlsx')">📥 7 วัน</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/payments?days=30', 'payments_30d.xlsx')">📥 30 วัน</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/payments?days=90', 'payments_90d.xlsx')">📥 90 วัน</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/payments?days=30&status=CONFIRMED', 'payments_confirmed_30d.xlsx')">📥 Confirmed 30d</button>
                </div>
            </div>

            <div class="detail-panel">
                <div style="font-weight:600;margin-bottom:0.5rem;color:var(--text);">👥 Customers</div>
                <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/customers?status=all', 'customers_all.xlsx')">📥 ทั้งหมด</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/customers?status=active', 'customers_active.xlsx')">📥 Active</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/customers?status=expired', 'customers_expired.xlsx')">📥 Expired</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/customers?status=banned', 'customers_banned.xlsx')">📥 Banned</button>
                </div>
            </div>

            <div class="detail-panel">
                <div style="font-weight:600;margin-bottom:0.5rem;color:var(--text);">📋 Subscriptions</div>
                <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/subscriptions?status=ACTIVE', 'subs_active.xlsx')">📥 Active</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/subscriptions?status=EXPIRED', 'subs_expired.xlsx')">📥 Expired</button>
                    <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/subscriptions', 'subs_all.xlsx')">📥 ทั้งหมด</button>
                </div>
            </div>

            <div class="detail-panel">
                <div style="font-weight:600;margin-bottom:0.5rem;color:var(--text);">🎯 Marketing</div>
                <button class="btn btn-sm btn-outline" onclick="downloadAuthed('/exports/marketing-links', 'marketing_links.xlsx')">📥 Marketing Links + ROI</button>
            </div>
        </div>
    `);
}

// ===== Sprint 2.6: Subscription manipulation handlers =====
async function customerCancelSub(uid) {
    const reason = prompt('เหตุผลที่ยกเลิก sub:');
    if (reason === null) return;
    try {
        await api(`/customers/${uid}/cancel-sub`, {
            method: 'POST',
            body: JSON.stringify({ reason: reason || '', refund_kept_days: false }),
        });
        toast('✅ ยกเลิก sub เรียบร้อย', 'success');
        showCustomer360(uid);
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function customerReactivateSub(uid) {
    if (!await confirmModal({ message: 'Reactivate sub ที่ยกเลิกล่าสุด? (ใช้ได้ถ้า end_date ยังไม่หมดจริง)', dangerous: true })) return;
    try {
        await api(`/customers/${uid}/reactivate-sub`, { method: 'POST' });
        toast('✅ Reactivate เรียบร้อย', 'success');
        showCustomer360(uid);
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function customerGiftSub(uid) {
    try {
        const pkgs = await api('/settings/packages');
        const opts = pkgs.map(p => `<option value="${p.id}">${esc(p.name)} (${fmtBaht(p.price)})</option>`).join('');
        openModal('🎁 Gift Subscription', `
            <p style="font-size:0.875rem;color:var(--text-muted);margin-bottom:1rem;">มอบ sub ฟรีให้ลูกค้า — ไม่นับเป็นรายได้ (no payment_id)</p>
            <div class="form-group">
                <label>แพ็กเกจ</label>
                <select id="gift-pkg">${opts}</select>
            </div>
            <div class="form-group">
                <label>จำนวนวัน (1-365)</label>
                <input type="number" id="gift-days" min="1" max="365" value="30">
            </div>
            <div class="form-group">
                <label>เหตุผล</label>
                <input id="gift-reason" placeholder="เช่น: ชดเชย bug / promo / VIP จริง">
            </div>
            <button class="btn btn-primary btn-full" onclick="doCustomerGiftSub(${uid})">✅ มอบ Gift</button>
        `);
    } catch (err) {
        toast('❌ ' + (err.message || 'load failed'), 'error');
    }
}

async function doCustomerGiftSub(uid) {
    try {
        const package_id = parseInt(document.getElementById('gift-pkg').value);
        const days = parseInt(document.getElementById('gift-days').value);
        const reason = document.getElementById('gift-reason').value.trim();
        if (!package_id) { toast('เลือกแพ็กเกจ', 'error'); return; }
        await api(`/customers/${uid}/gift-sub`, {
            method: 'POST',
            body: JSON.stringify({ package_id, days, reason }),
        });
        toast('✅ มอบ gift เรียบร้อย', 'success');
        closeModal();
        showCustomer360(uid);
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

// ===== Sprint 3.4: Bulk approve/reject =====
window._inboxSelected = new Set();

function inboxToggle(id) {
    if (window._inboxSelected.has(id)) window._inboxSelected.delete(id);
    else window._inboxSelected.add(id);
    updateInboxBulkBar();
}

function updateInboxBulkBar() {
    let bar = document.getElementById('inbox-bulk-bar');
    const count = window._inboxSelected.size;
    if (count === 0) {
        if (bar) bar.remove();
        return;
    }
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'inbox-bulk-bar';
        bar.style.cssText = 'position:fixed;bottom:1rem;left:50%;transform:translateX(-50%);background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:0.625rem 1rem;box-shadow:var(--shadow-lg);z-index:200;display:flex;gap:0.5rem;align-items:center;';
        document.body.appendChild(bar);
    }
    bar.innerHTML = `
        <span style="font-size:0.875rem;font-weight:500;color:var(--text);">เลือก ${count} รายการ</span>
        <button class="btn btn-success btn-sm" onclick="inboxBulkApprove()">✅ Approve ทั้งหมด</button>
        <button class="btn btn-danger btn-sm" onclick="inboxBulkReject()">❌ Reject ทั้งหมด</button>
        <button class="btn btn-outline btn-sm" onclick="window._inboxSelected.clear();updateInboxBulkBar();renderInbox();">ยกเลิก</button>
    `;
}

async function inboxBulkApprove() {
    const ids = Array.from(window._inboxSelected);
    // Phase A.2 (2026-06-27): stronger confirm with consequences
    const msg = `✅ Approve ${ids.length} สลิป พร้อมกัน?\n\n\n` +
        `จะเกิดอะไรขึ้น:\n` +
        `  • ลูกค้า ${ids.length} คนจะได้ VIP/Gacha ทันที\n` +
        `  • DM ส่งไปทุกคนพร้อม invite link\n` +
        `  • Subscription/credit สร้างใน DB\n\n` +
        `⚠️ Action นี้กลับไม่ได้ — ถ้ามีสลิปปลอมหลุดผ่าน ลูกค้าจะได้ VIP ฟรี`;
    if (!await confirmModal({ message: msg, dangerous: true })) return;
    try {
        const result = await api('/payments/bulk-approve', {
            method: 'POST',
            body: JSON.stringify({ payment_ids: ids }),
        });
        const ok = result.approved?.length || 0;
        const fail = result.failed?.length || 0;
        toast(`✅ ยืนยัน ${ok} รายการ${fail > 0 ? ' / ❌ ล้มเหลว ' + fail : ''}`, fail === 0 ? 'success' : 'info');
        window._inboxSelected.clear();
        updateInboxBulkBar();
        renderInbox();
    } catch (err) {
        toast('❌ ' + (err.message || 'fail'), 'error');
    }
}

async function inboxBulkReject() {
    const ids = Array.from(window._inboxSelected);
    // Phase A.2 (2026-06-27): show count + warn
    const reason = prompt(`❌ Reject ${ids.length} สลิป พร้อมกัน\n\nกรอกเหตุผล (จะส่ง DM ให้ลูกค้าทุกคน):`);
    if (reason === null || !reason.trim()) {
        if (reason !== null) toast('กรุณาใส่เหตุผล', 'error');
        return;
    }
    try {
        const result = await api('/payments/bulk-reject', {
            method: 'POST',
            body: JSON.stringify({ payment_ids: ids, reason }),
        });
        const ok = result.rejected?.length || 0;
        const fail = result.failed?.length || 0;
        toast(`❌ ปฏิเสธ ${ok} รายการ${fail > 0 ? ' / ล้มเหลว ' + fail : ''}`, fail === 0 ? 'success' : 'info');
        window._inboxSelected.clear();
        updateInboxBulkBar();
        renderInbox();
    } catch (err) {
        toast('❌ ' + (err.message || 'fail'), 'error');
    }
}

// ========== PAGE: PRAE LOGS ==========
var praeLogsDays = 7;

async function renderPraeLogs() {
    const area = document.getElementById('page-content');
    area.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    try {
        const [summary, topUsers] = await Promise.all([
            api(`/prae-logs/summary?days=${praeLogsDays}`),
            api(`/prae-logs/top-users?days=${praeLogsDays}&limit=30`),
        ]);
        const cost_thb = (summary.total_cost_usd || 0) * 35;

        area.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
                <h2 style="margin:0;font-size:1.25rem;font-weight:600;">💬 Prae Logs</h2>
                <div class="filters">
                    <button class="filter-btn ${praeLogsDays===1?'active':''}" onclick="praeLogsDays=1;renderPraeLogs()">1 วัน</button>
                    <button class="filter-btn ${praeLogsDays===7?'active':''}" onclick="praeLogsDays=7;renderPraeLogs()">7 วัน</button>
                    <button class="filter-btn ${praeLogsDays===30?'active':''}" onclick="praeLogsDays=30;renderPraeLogs()">30 วัน</button>
                </div>
            </div>

            <div class="mini-cards" style="margin-bottom:1rem;">
                <div class="mini-card"><div class="mini-card-label">📨 ข้อความรวม</div><div class="mini-card-value">${fmt(summary.total_msgs || 0)}</div></div>
                <div class="mini-card"><div class="mini-card-label">👥 คนคุย</div><div class="mini-card-value">${fmt(summary.unique_users || 0)}</div></div>
                <div class="mini-card"><div class="mini-card-label">💸 Cost (USD)</div><div class="mini-card-value">$${(summary.total_cost_usd||0).toFixed(3)}</div></div>
                <div class="mini-card"><div class="mini-card-label">≈ THB</div><div class="mini-card-value">฿${cost_thb.toFixed(0)}</div></div>
            </div>

            <div class="card card-full">
                <div class="card-label">👥 Top คน Prae คุยเยอะสุด (${praeLogsDays} วัน)</div>
                <div class="table-wrap" style="max-height:60vh;overflow:auto;">
                    <table style="width:100%;font-size:0.85rem;">
                        <thead style="background:var(--surface-2);position:sticky;top:0;">
                            <tr>
                                <th style="padding:0.5rem;text-align:left;">ลูกค้า</th>
                                <th style="padding:0.5rem;text-align:left;">Telegram</th>
                                <th style="padding:0.5rem;text-align:right;">ข้อความ</th>
                                <th style="padding:0.5rem;text-align:right;">Cost USD</th>
                                <th style="padding:0.5rem;text-align:right;">≈ THB</th>
                                <th style="padding:0.5rem;text-align:left;">ล่าสุด</th>
                                <th style="padding:0.5rem;text-align:center;"></th>
                            </tr>
                        </thead>
                        <tbody>
                            ${topUsers.map(u => `
                            <tr style="border-top:1px solid var(--border);">
                                <td style="padding:0.5rem;">${esc(u.first_name || '?')} ${esc(u.last_name || '')} ${u.username ? '<small style="color:var(--text-muted);">@'+esc(u.username)+'</small>' : ''}</td>
                                <td style="padding:0.5rem;font-family:var(--font-mono);font-size:0.75rem;">${u.telegram_id}</td>
                                <td style="padding:0.5rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(u.msgs)}</td>
                                <td style="padding:0.5rem;text-align:right;font-variant-numeric:tabular-nums;">$${(u.total_cost_usd||0).toFixed(4)}</td>
                                <td style="padding:0.5rem;text-align:right;font-variant-numeric:tabular-nums;color:var(--text-muted);">฿${((u.total_cost_usd||0)*35).toFixed(1)}</td>
                                <td style="padding:0.5rem;font-size:0.75rem;color:var(--text-muted);">${fmtDateTime(u.last_msg_at)}</td>
                                <td style="padding:0.5rem;text-align:center;">
                                    <button class="btn btn-sm btn-outline" onclick="openPraeConvo(${u.telegram_id})">💬 ดูแชท</button>
                                </td>
                            </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    } catch (err) {
        area.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function openPraeConvo(telegramId) {
    try {
        const data = await api(`/prae-logs/conversation/${telegramId}?limit=100`);
        const u = data.user || {};
        const msgs = data.messages || [];

        // Phase A.2 fix: render Prae's HTML (assistant only) — user messages still escaped
        // + tools collapsed into a ⚙️ details element (hidden by default)
        const renderPraeBody = (content) => {
            if (typeof content !== 'string') return '';
            // Strip script/iframe + only allow safe tags
            const tmp = document.createElement('div');
            tmp.innerHTML = content;
            tmp.querySelectorAll('script,iframe,style').forEach(e => e.remove());
            return tmp.innerHTML;
        };
        const bubbles = msgs.map(m => {
            const isPrae = m.role === 'assistant';
            const toolsObj = m.tools_used && Object.keys(m.tools_used).length > 0 ? m.tools_used : null;
            const tools = toolsObj
                ? `<details style="margin-top:0.3rem;"><summary style="font-size:0.7rem;color:var(--text-muted);cursor:pointer;">⚙️ tools (${Object.keys(toolsObj).length})</summary><div style="font-size:0.7rem;color:var(--text-muted);margin-top:0.25rem;font-family:var(--font-mono);">${esc(JSON.stringify(toolsObj).slice(0,400))}</div></details>` : '';
            const cost = m.cost_usd > 0 ? `<span style="font-size:0.65rem;color:var(--text-muted);margin-left:0.5rem;">$${m.cost_usd.toFixed(5)}</span>` : '';
            const bodyHtml = isPrae ? renderPraeBody(m.content) : esc(m.content);
            return `
                <div style="display:flex;${isPrae?'justify-content:flex-start':'justify-content:flex-end'};margin-bottom:0.5rem;">
                    <div style="max-width:75%;padding:0.5rem 0.75rem;border-radius:12px;background:${isPrae?'var(--surface-2)':'var(--accent)'};color:${isPrae?'var(--text)':'#fff'};font-size:0.875rem;">
                        <div style="font-size:0.7rem;opacity:0.7;margin-bottom:0.2rem;">${isPrae?'🤖 Prae':'👤 ' + esc(u.first_name || 'User')} <span style="opacity:0.6;">${fmtDateTime(m.created_at)}</span>${cost}</div>
                        <div style="white-space:pre-wrap;word-break:break-word;">${bodyHtml}</div>
                        ${tools}
                    </div>
                </div>
            `;
        }).join('');

        openModal(`💬 ${esc(u.first_name || '?')} ${esc(u.last_name || '')} (tg:${telegramId})`, `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
                <small style="color:var(--text-muted);">${msgs.length} ข้อความ</small>
                ${u.id ? `<button class="btn btn-sm btn-outline" onclick="closeModal();showCustomer360(${u.id})">👤 Customer 360</button>` : ''}
            </div>
            <div id="prae-bubbles-${telegramId}" style="max-height:55vh;overflow:auto;padding:0.5rem;background:var(--surface);border-radius:8px;margin-bottom:0.5rem;">
                ${bubbles || '<div style="text-align:center;padding:2rem;color:var(--text-muted);">ไม่มีข้อความ</div>'}
            </div>
            ${u.id ? `
            <div style="border-top:1px solid var(--border);padding-top:0.5rem;">
                <div style="display:flex;gap:0.4rem;align-items:flex-end;">
                    <textarea id="prae-dm-input-${u.id}" placeholder="พิมพ์ข้อความส่งหาลูกค้า... (รองรับ HTML <b>, <i>, <a>)" rows="2" style="flex:1;font-size:0.85rem;padding:0.5rem;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);line-height:1.4;resize:vertical;"></textarea>
                    <button class="btn btn-primary btn-sm" onclick="sendPraeDM(${u.id}, ${telegramId})" id="prae-dm-send-${u.id}" style="white-space:nowrap;">📤 ส่ง</button>
                </div>
                <div style="font-size:0.7rem;color:var(--text-muted);margin-top:0.25rem;">
                    ⚠️ ส่งจาก @NamwarnJarern_bot · ไม่ผ่าน Prae AI · ข้อความจะถูกบันทึกใน timeline
                </div>
            </div>` : ''}
        `, { wide: true });
    } catch (err) {
        toast('❌ ' + (err.message || 'load failed'), 'error');
    }
}

// ===== Sprint 1.3: Unified Daily Report (single source of truth) =====
async function openDailyReportModal() {
    try {
        const d = await api('/daily-report/today');
        const diffIcon = d.diff_revenue >= 0 ? '📈' : '📉';
        const diffColor = d.diff_revenue >= 0 ? 'var(--success)' : 'var(--error)';
        const diffSign = d.diff_revenue >= 0 ? '+' : '';

        const pkgs = (d.top_packages || []).map(p => `
            <tr style="border-top:1px solid var(--border);">
                <td style="padding:0.4rem;">${esc(p.name)}</td>
                <td style="padding:0.4rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(p.sold)}</td>
                <td style="padding:0.4rem;text-align:right;font-variant-numeric:tabular-nums;color:var(--primary);">${fmtBaht(p.revenue)}</td>
            </tr>
        `).join('');

        openModal(`📋 รายงานวันนี้ — ${d.date_bkk}`, `
            <div style="background:linear-gradient(135deg,var(--accent-light),transparent);padding:1rem;border-radius:12px;margin-bottom:1rem;">
                <div style="font-size:0.75rem;color:var(--text-muted);">รายได้วันนี้</div>
                <div style="font-size:2rem;font-weight:700;color:var(--primary);">${fmtBaht(d.today.revenue)}</div>
                <div style="font-size:0.875rem;color:${diffColor};margin-top:0.25rem;">
                    ${diffIcon} ${diffSign}${fmtBaht(d.diff_revenue)} (${diffSign}${d.diff_pct.toFixed(1)}%) vs เมื่อวาน
                </div>
            </div>

            <div class="mini-cards" style="margin-bottom:1rem;">
                <div class="mini-card"><div class="mini-card-label">ออเดอร์</div><div class="mini-card-value">${fmt(d.today.orders)}</div></div>
                <div class="mini-card"><div class="mini-card-label">ลูกค้าใหม่</div><div class="mini-card-value">${fmt(d.new_users)}</div></div>
                <div class="mini-card"><div class="mini-card-label">รออนุมัติ</div><div class="mini-card-value" style="color:var(--warning);">${fmt(d.today.pending)}</div></div>
                <div class="mini-card"><div class="mini-card-label">SOS เปิด</div><div class="mini-card-value" style="color:${d.sos_open > 0 ? 'var(--error)' : 'var(--text-muted)'};">${fmt(d.sos_open)}</div></div>
            </div>

            <div class="card" style="margin-bottom:1rem;">
                <div class="card-label">📋 Subscriptions</div>
                <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.5rem;font-size:0.875rem;">
                    <div><div style="color:var(--text-muted);">Active</div><div style="font-size:1.25rem;font-weight:600;">${fmt(d.subscriptions.active)}</div></div>
                    <div><div style="color:var(--text-muted);">หมดใน 7 วัน</div><div style="font-size:1.25rem;font-weight:600;color:var(--warning);">${fmt(d.subscriptions.expiring_7d)}</div></div>
                    <div><div style="color:var(--text-muted);">หมดใน 24 ชม.</div><div style="font-size:1.25rem;font-weight:600;color:var(--error);">${fmt(d.subscriptions.expiring_24h)}</div></div>
                </div>
            </div>

            ${pkgs ? `
            <div class="card">
                <div class="card-label">🏆 แพ็กเกจขายดีวันนี้</div>
                <table style="width:100%;font-size:0.875rem;">
                    <thead style="background:var(--surface-2);">
                        <tr>
                            <th style="padding:0.4rem;text-align:left;">แพ็กเกจ</th>
                            <th style="padding:0.4rem;text-align:right;">ขายได้</th>
                            <th style="padding:0.4rem;text-align:right;">รายได้</th>
                        </tr>
                    </thead>
                    <tbody>${pkgs}</tbody>
                </table>
            </div>
            ` : ''}

            <div style="margin-top:1rem;padding:0.625rem;background:var(--surface-2);border-radius:8px;font-size:0.75rem;color:var(--text-muted);">
                💡 รายงานนี้ใช้ข้อมูลเดียวกันกับ Discord report (08:00) + Telegram daily summary (23:59).<br>
                หมายเหตุ: filter <code>amount > 0</code> + <code>telegram_id < 9000000000</code> เพื่อตัด test users
            </div>
        `);
    } catch (err) {
        toast('❌ ' + (err.message || 'load failed'), 'error');
    }
}

// ===== Sprint 3.3: Mobile drawer + bottom nav =====
function toggleMobileSidebar() {
    const sb = document.querySelector('.sidebar');
    if (!sb) return;
    sb.classList.toggle('mobile-open');
    document.body.classList.toggle('has-mobile-overlay');
}

function setupMobile() {
    // Add burger button to top-bar-left (before page-title)
    const topBar = document.querySelector('.top-bar');
    if (!topBar) return;
    if (!document.querySelector('.mobile-burger')) {
        const burger = document.createElement('button');
        burger.className = 'mobile-burger';
        burger.innerHTML = '☰';
        burger.onclick = toggleMobileSidebar;
        topBar.insertBefore(burger, topBar.firstChild);
    }

    // Close drawer when clicking page content
    document.body.addEventListener('click', (e) => {
        const sb = document.querySelector('.sidebar.mobile-open');
        if (sb && !sb.contains(e.target) && !e.target.closest('.mobile-burger')) {
            sb.classList.remove('mobile-open');
            document.body.classList.remove('has-mobile-overlay');
        }
    });

    // Add bottom nav (mobile only)
    if (!document.querySelector('.mobile-bottom-nav')) {
        const items = [
            {id:'overview', icon:'📊', label:'ภาพรวม'},
            {id:'inbox', icon:'📥', label:'รอจัดการ'},
            {id:'customers', icon:'👥', label:'ลูกค้า'},
            {id:'finance', icon:'💰', label:'รายได้'},
            {id:'settings', icon:'⚙️', label:'ตั้งค่า'},
        ];
        const nav = document.createElement('div');
        nav.className = 'mobile-bottom-nav';
        nav.innerHTML = items.map(it => `
            <button class="nav-btn" data-page="${it.id}" onclick="navigate('${it.id}')">
                <span class="icon">${it.icon}</span>
                <span>${it.label}</span>
            </button>
        `).join('');
        document.body.appendChild(nav);
    }
}

// Hook: refresh bottom nav active state on navigate
const _orig_navigate = window.navigate;
if (_orig_navigate) {
    window.navigate = function(page) {
        _orig_navigate(page);
        // close drawer
        const sb = document.querySelector('.sidebar.mobile-open');
        if (sb) { sb.classList.remove('mobile-open'); document.body.classList.remove('has-mobile-overlay'); }
        // update bottom nav active
        document.querySelectorAll('.mobile-bottom-nav .nav-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.page === page);
        });
    };
}

// Initialize on DOM ready
if (document.readyState !== 'loading') setupMobile();
else document.addEventListener('DOMContentLoaded', setupMobile);

// ===== Sprint 3.1: Cmd+K Command Palette =====
const CMDK_ACTIONS = [
    // Navigation
    { type: 'nav', icon: '📊', label: 'ภาพรวม', hint: 'overview', action: () => navigate('overview') },
    { type: 'nav', icon: '📥', label: 'กล่องรอจัดการ', hint: 'inbox', action: () => navigate('inbox') },
    { type: 'nav', icon: '👥', label: 'ลูกค้า', hint: 'customers', action: () => navigate('customers') },
    { type: 'nav', icon: '💰', label: 'รายได้', hint: 'finance/revenue', action: () => navigate('finance') },
    { type: 'nav', icon: '📋', label: 'แพ็กเกจ', hint: 'packages', action: () => navigate('packages') },
    { type: 'nav', icon: '🎯', label: 'การตลาด', hint: 'marketing', action: () => navigate('marketing') },
    { type: 'nav', icon: '💳', label: 'บัญชีรับเงิน', hint: 'receivers', action: () => navigate('receivers') },
    { type: 'nav', icon: '🎰', label: 'กาชา', hint: 'gacha', action: () => navigate('gacha') },
    { type: 'nav', icon: '💬', label: 'Prae Logs', hint: 'prae conversation', action: () => navigate('prae_logs') },
    { type: 'nav', icon: '⚙️', label: 'ตั้งค่า', hint: 'settings', action: () => navigate('settings') },
    // Quick actions
    { type: 'action', icon: '📋', label: 'รายงานวันนี้', hint: 'daily report', action: () => openDailyReportModal() },
    { type: 'action', icon: '📥', label: 'Export Excel...', hint: 'open exports modal', action: () => openExportsModal() },
    { type: 'action', icon: '🌓', label: 'สลับธีม', hint: 'light / dark', action: () => toggleTheme && toggleTheme() },
    { type: 'action', icon: '🚪', label: 'ออกจากระบบ', hint: 'logout', action: () => logout && logout() },
];

let _cmdkOpen = false;
let _cmdkResults = [];
let _cmdkCursor = 0;

function openCmdK() {
    if (_cmdkOpen) return;
    _cmdkOpen = true;
    let overlay = document.getElementById('cmdk-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'cmdk-overlay';
        overlay.className = 'cmdk-overlay';
        overlay.innerHTML = `
            <div class="cmdk-modal" onclick="event.stopPropagation()">
                <input type="text" id="cmdk-input" class="cmdk-input" placeholder="ค้นหา ลูกค้า / payment / หน้า / คำสั่ง...">
                <div id="cmdk-results" class="cmdk-results"></div>
                <div class="cmdk-hint-bar">
                    <span><kbd>↑↓</kbd> เลือก <kbd>↵</kbd> ทำ <kbd>Esc</kbd> ปิด</span>
                    <span><kbd>Ctrl</kbd>+<kbd>K</kbd></span>
                </div>
            </div>
        `;
        overlay.onclick = closeCmdK;
        document.body.appendChild(overlay);
    }
    overlay.classList.add('open');
    const input = document.getElementById('cmdk-input');
    input.value = '';
    input.focus();
    input.addEventListener('input', cmdkSearch);
    input.addEventListener('keydown', cmdkKeydown);
    cmdkSearch();
}

function closeCmdK() {
    _cmdkOpen = false;
    const overlay = document.getElementById('cmdk-overlay');
    if (overlay) overlay.classList.remove('open');
}

async function cmdkSearch() {
    const q = (document.getElementById('cmdk-input')?.value || '').trim().toLowerCase();
    const resultsEl = document.getElementById('cmdk-results');
    if (!resultsEl) return;

    _cmdkResults = [];
    _cmdkCursor = 0;

    // Filter built-in actions
    const actions = CMDK_ACTIONS.filter(a => {
        if (!q) return true;
        return (a.label + ' ' + a.hint).toLowerCase().includes(q);
    });
    _cmdkResults.push(...actions);

    // If query looks like a search, fire customer + payment search
    if (q.length >= 2) {
        try {
            // Customer search (name/phone/tg id)
            const r = await api(`/customers?q=${encodeURIComponent(q)}&limit=8`);
            const customers = (r.items || r || []).slice(0, 5);
            customers.forEach(c => {
                _cmdkResults.push({
                    type: 'customer',
                    icon: '👤',
                    label: `${c.first_name || ''} ${c.last_name || ''}`.trim() || c.username || `tg:${c.telegram_id}`,
                    hint: `${c.username ? '@'+c.username : 'tg:'+c.telegram_id} · ${fmtBaht(c.total_spent || 0)}`,
                    action: () => { closeCmdK(); showCustomer360(c.id); },
                });
            });
        } catch (err) {}
    }

    // If query is a number ≥4 digits, try payment id
    if (/^\d{4,}$/.test(q)) {
        try {
            const p = await api(`/payments/${q}`);
            if (p && p.id) {
                _cmdkResults.unshift({
                    type: 'payment',
                    icon: '💰',
                    label: `Payment #${p.id} — ${fmtBaht(p.amount)} (${p.status})`,
                    hint: 'open payment',
                    action: () => { closeCmdK(); openSlipImage(p.id); },
                });
            }
        } catch (err) {}
    }

    if (_cmdkResults.length === 0) {
        resultsEl.innerHTML = '<div class="cmdk-empty">ไม่พบผลลัพธ์</div>';
        return;
    }

    let lastType = null;
    let html = '';
    _cmdkResults.forEach((r, idx) => {
        if (r.type !== lastType) {
            const labels = { nav: '📍 หน้า', action: '⚡ คำสั่ง', customer: '👤 ลูกค้า', payment: '💰 Payment' };
            html += `<div class="cmdk-section-label">${labels[r.type] || r.type}</div>`;
            lastType = r.type;
        }
        html += `
            <div class="cmdk-item ${idx === _cmdkCursor ? 'active' : ''}" data-idx="${idx}" onclick="cmdkRun(${idx})">
                <span class="icon">${r.icon}</span>
                <span class="label">${esc(r.label)}</span>
                <span class="hint">${esc(r.hint || '')}</span>
            </div>
        `;
    });
    resultsEl.innerHTML = html;
}

function cmdkRun(idx) {
    const r = _cmdkResults[idx];
    if (!r) return;
    closeCmdK();
    setTimeout(() => r.action && r.action(), 50);
}

function cmdkKeydown(e) {
    if (e.key === 'Escape') { closeCmdK(); return; }
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        _cmdkCursor = Math.min(_cmdkCursor + 1, _cmdkResults.length - 1);
        cmdkRefreshCursor();
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        _cmdkCursor = Math.max(_cmdkCursor - 1, 0);
        cmdkRefreshCursor();
    } else if (e.key === 'Enter') {
        e.preventDefault();
        cmdkRun(_cmdkCursor);
    }
}

function cmdkRefreshCursor() {
    document.querySelectorAll('.cmdk-item').forEach((el, idx) => {
        el.classList.toggle('active', idx === _cmdkCursor);
        if (idx === _cmdkCursor) el.scrollIntoView({ block: 'nearest' });
    });
}

// Global hotkey: Ctrl/Cmd+K
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        if (_cmdkOpen) closeCmdK();
        else openCmdK();
    }
});

// ===== Sprint 3.2: Real-time WebSocket =====
let _wsConn = null;
let _wsReconnectTimer = null;

function startLiveUpdates() {
    if (!token) return;
    if (_wsConn && _wsConn.readyState <= 1) return;

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/ws/events?token=${encodeURIComponent(token)}`;
    try {
        _wsConn = new WebSocket(url);
    } catch (err) {
        console.warn('[WS] open failed:', err);
        return;
    }

    _wsConn.onopen = () => {
        console.log('[WS] connected');
        // Show small live indicator
        let dot = document.getElementById('ws-live-dot');
        if (!dot) {
            dot = document.createElement('span');
            dot.id = 'ws-live-dot';
            dot.style.cssText = 'display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--success);margin-right:0.35rem;animation:pulseDot 1.5s infinite;vertical-align:middle;';
            dot.title = '🟢 ระบบ Live Update ทำงาน — แจ้งเตือนทันทีเมื่อมีสลิปใหม่/SOS';
            const right = document.querySelector('.top-bar-right');
            if (right) right.insertBefore(dot, right.firstChild);
        }
    };

    _wsConn.onmessage = (ev) => {
        try {
            const m = JSON.parse(ev.data);
            handleWsMessage(m);
        } catch (err) {}
    };

    _wsConn.onclose = () => {
        console.log('[WS] disconnected — retry in 5s');
        const dot = document.getElementById('ws-live-dot');
        if (dot) dot.style.background = 'var(--text-muted)';
        clearTimeout(_wsReconnectTimer);
        _wsReconnectTimer = setTimeout(startLiveUpdates, 5000);
    };

    _wsConn.onerror = (err) => {
        console.warn('[WS] error:', err);
    };
}

function handleWsMessage(m) {
    if (m.type === 'new_payment') {
        toast(`💰 Payment ใหม่ #${m.id} — กล่องรอจัดการ: ${m.total_pending} รายการ`, 'info');
        // If inbox page open, refresh
        if (window.__currentPage === 'inbox' && typeof renderInbox === 'function') {
            renderInbox();
        }
        // Update alert badge counter
        const badge = document.getElementById('alert-badge');
        const cnt = document.getElementById('alert-count');
        if (badge && cnt) {
            cnt.textContent = m.total_pending;
            badge.classList.remove('hidden');
        }
    } else if (m.type === 'new_sos') {
        toast(`🚨 SOS ใหม่ #${m.id} — เปิดอยู่: ${m.total_open}`, 'error', 8000);
        if (window.__currentPage === 'inbox' && typeof renderInbox === 'function') {
            renderInbox();
        }
    } else if (m.type === 'tick') {
        // Update badge from snapshot
        const badge = document.getElementById('alert-badge');
        const cnt = document.getElementById('alert-count');
        const total = (m.data?.pending_payments || 0) + (m.data?.open_sos || 0);
        if (badge && cnt) {
            cnt.textContent = total;
            badge.classList.toggle('hidden', total === 0);
        }
    }
}

// Inject pulse animation
if (!document.getElementById('ws-pulse-style')) {
    const s = document.createElement('style');
    s.id = 'ws-pulse-style';
    s.textContent = '@keyframes pulseDot { 0%,100%{opacity:1} 50%{opacity:0.4} }';
    document.head.appendChild(s);
}

// Start when authenticated
const _origRenderLogin = window.renderLogin;
function _kickoffLiveAfterLogin() {
    if (token && !_wsConn) {
        setTimeout(startLiveUpdates, 1000);
    }
}
// Auto-start if already logged in
if (typeof token !== 'undefined' && token) {
    setTimeout(_kickoffLiveAfterLogin, 2000);
}
// Hook navigate so we know the current page
const _navOrig = window.navigate;
if (_navOrig) {
    window.navigate = function(page) {
        window.__currentPage = page;
        _navOrig(page);
    };
}

// ===== Sprint 2.5: Prae Prompt Editor =====
async function loadPraePrompt() {
    const area = document.getElementById('settings-area');
    if (!area) return;
    area.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    try {
        const [current, versions] = await Promise.all([
            api('/prae-prompt/active'),
            api('/prae-prompt/versions'),
        ]);

        const isOverride = current.active_source === 'file' || current.override_exists;
        const versionsHtml = versions.length === 0
            ? '<div style="text-align:center;padding:1rem;color:var(--text-muted);font-size:0.875rem;">ยังไม่มีเวอร์ชันที่เซฟ</div>'
            : `
                <table style="width:100%;font-size:0.85rem;">
                    <thead style="background:var(--surface-2);">
                        <tr>
                            <th style="padding:0.4rem;text-align:left;">v</th>
                            <th style="padding:0.4rem;text-align:left;">Notes</th>
                            <th style="padding:0.4rem;text-align:right;">ขนาด</th>
                            <th style="padding:0.4rem;text-align:left;">บันทึกเมื่อ</th>
                            <th style="padding:0.4rem;text-align:center;">Status</th>
                            <th style="padding:0.4rem;text-align:center;"></th>
                        </tr>
                    </thead>
                    <tbody>
                        ${versions.map(v => `
                            <tr style="border-top:1px solid var(--border);${v.is_active?'background:rgba(0,200,100,0.06);':''}">
                                <td style="padding:0.4rem;">v${v.version}</td>
                                <td style="padding:0.4rem;font-size:0.78rem;color:var(--text-muted);">${esc(v.notes || '-')}</td>
                                <td style="padding:0.4rem;text-align:right;font-variant-numeric:tabular-nums;">${fmt(v.char_count)}</td>
                                <td style="padding:0.4rem;font-size:0.78rem;color:var(--text-muted);">${fmtDateTime(v.created_at)}</td>
                                <td style="padding:0.4rem;text-align:center;">${v.is_active ? '🟢 active' : '⚪'}</td>
                                <td style="padding:0.4rem;text-align:center;">
                                    <button class="btn btn-sm btn-outline" onclick="previewPromptVersion(${v.id})" title="ดู">👁</button>
                                    ${!v.is_active ? `<button class="btn btn-sm btn-outline" onclick="activatePromptVersion(${v.id})" title="ใช้เวอร์ชันนี้">✅</button>` : ''}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;

        area.innerHTML = `
            <div style="margin-bottom:1rem;padding:0.75rem;background:${isOverride?'rgba(0,200,100,0.08)':'var(--surface-2)'};border-radius:8px;font-size:0.85rem;">
                <strong>${isOverride ? '🟢 ใช้ prompt ที่บอสตั้งจาก dashboard' : '⚪ ใช้ prompt ค่าเริ่มต้นจากโค้ด'}</strong>
                <span style="color:var(--text-muted);margin-left:0.5rem;">— ${fmt(current.char_count)} ตัวอักษร</span>
            </div>

            <div class="card" style="margin-bottom:1rem;">
                <div class="card-label">✏️ แก้บุคลิก Prae</div>
                <textarea id="prae-prompt-textarea" rows="20" style="width:100%;font-family:var(--font-mono);font-size:0.8rem;padding:0.625rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);line-height:1.5;resize:vertical;">${esc(current.content)}</textarea>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:0.75rem;">
                    <input id="prae-prompt-notes" placeholder="โน้ตการเปลี่ยน (เช่น: ปรับความสุภาพ + เพิ่ม emoji)" style="flex:1;margin-right:0.5rem;">
                    <button class="btn btn-primary btn-sm" onclick="savePraePrompt(true)">💾 บันทึก + Activate</button>
                    <button class="btn btn-outline btn-sm" onclick="savePraePrompt(false)" style="margin-left:0.5rem;">💾 บันทึกเฉยๆ</button>
                </div>
                <div style="margin-top:0.5rem;display:flex;justify-content:space-between;align-items:center;">
                    <small style="color:var(--text-muted);">⚠️ Cache 60 วินาที — เปลี่ยนแล้วรอ 1 นาทีถึงจะ effect</small>
                    ${isOverride ? '<button class="btn btn-sm btn-outline" style="color:var(--error);" onclick="resetPraePrompt()">🔄 รีเซ็ตเป็นค่าเริ่มต้น</button>' : ''}
                </div>
            </div>

            <div class="card">
                <div class="card-label">📜 ประวัติเวอร์ชัน (${versions.length})</div>
                ${versionsHtml}
            </div>
        `;
    } catch (err) {
        area.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${esc(err.message)}</p></div>`;
    }
}

async function savePraePrompt(activate) {
    const content = document.getElementById('prae-prompt-textarea').value;
    const notes = document.getElementById('prae-prompt-notes').value.trim();
    if (!content.trim()) { toast('content ว่าง', 'error'); return; }
    if (activate && !await confirmModal({ message: 'Activate prompt ใหม่นี้ไหม?\n\nบอท Prae จะใช้ prompt ใหม่ภายใน 1 นาที', dangerous: true })) return;
    try {
        const r = await api('/prae-prompt/save', {
            method: 'POST',
            body: JSON.stringify({ content, notes, activate }),
        });
        toast(`✅ บันทึก v${r.version}${activate ? ' (active)' : ''}`, 'success');
        loadPraePrompt();
    } catch (err) {
        toast('❌ ' + (err.message || 'save failed'), 'error');
    }
}

async function previewPromptVersion(vid) {
    try {
        const v = await api(`/prae-prompt/versions/${vid}`);
        openModal(`🤖 Prae Prompt v${v.version}`, `
            <div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.75rem;">
                ${esc(v.notes || '—')} · ${fmtDateTime(v.created_at)} · ${fmt(v.content.length)} chars
                ${v.is_active ? '<span style="color:var(--success);margin-left:0.5rem;">🟢 active</span>' : ''}
            </div>
            <pre style="background:var(--surface);padding:0.75rem;border-radius:8px;white-space:pre-wrap;font-size:0.8rem;line-height:1.5;max-height:60vh;overflow:auto;font-family:var(--font-mono);">${esc(v.content)}</pre>
            ${!v.is_active ? `<button class="btn btn-primary btn-full" style="margin-top:0.75rem;" onclick="closeModal();activatePromptVersion(${v.id})">✅ ใช้เวอร์ชันนี้</button>` : ''}
        `);
    } catch (err) {
        toast('❌ ' + (err.message || 'load failed'), 'error');
    }
}

async function activatePromptVersion(vid) {
    if (!await confirmModal({ message: 'Activate เวอร์ชันนี้? Prae จะใช้ prompt นี้ภายใน 1 นาที', dangerous: true })) return;
    try {
        await api(`/prae-prompt/activate/${vid}`, { method: 'POST' });
        toast('✅ Activated', 'success');
        loadPraePrompt();
    } catch (err) {
        toast('❌ ' + (err.message || 'activate failed'), 'error');
    }
}

async function resetPraePrompt() {
    if (!await confirmModal({ message: 'รีเซ็ตเป็น prompt ค่าเริ่มต้นจากโค้ด?\n\nเวอร์ชันที่บันทึกไว้จะยังคงอยู่ แต่จะไม่ active', dangerous: true })) return;
    try {
        await api('/prae-prompt/override', { method: 'DELETE' });
        toast('✅ Reset เรียบร้อย', 'success');
        loadPraePrompt();
    } catch (err) {
        toast('❌ ' + (err.message || 'reset failed'), 'error');
    }
}

// ===== Authed slip image viewer (Bearer token required) =====
// ===== Slip image + payment detail popup (boss requested popup not new tab) =====
async function openSlipImage(paymentId) {
    if (!paymentId) return;
    try {
        // Open modal early with loading state
        openModal(`💳 Payment #${paymentId}`, `
            <div id="slip-modal-body" style="text-align:center;padding:1rem;">
                <div class="spinner"></div>
                <div style="margin-top:0.5rem;color:var(--text-muted);font-size:0.875rem;">กำลังโหลด...</div>
            </div>
        `, { wide: true });

        // Fetch detail + slip in parallel
        const [detail, slipBlob] = await Promise.all([
            api(`/payments/${paymentId}/detail`),
            fetch(`/api/payments/${paymentId}/slip-image`, {
                headers: { Authorization: `Bearer ${token}` }
            }).then(r => r.ok ? r.blob() : null).catch(() => null),
        ]);

        const slipUrl = slipBlob ? URL.createObjectURL(slipBlob) : null;
        const body = document.getElementById('slip-modal-body');
        if (!body) return;

        // Format helpers
        const d = detail;
        const cust = d.customer || {};
        const pkg = d.package || {};
        const promo = d.promo;
        const slip = d.slip || {};
        const retry = d.retry;

        const statusColor = {
            PENDING: 'var(--warning)',
            CONFIRMED: 'var(--success)',
            REJECTED: 'var(--error)',
            REFUNDED: 'var(--text-muted)',
        }[d.status] || 'var(--text)';

        const statusBadge = `<span style="display:inline-block;padding:0.2rem 0.625rem;border-radius:9999px;background:${statusColor}22;color:${statusColor};font-size:0.75rem;font-weight:600;">${d.status}</span>`;

        const rankBadge = cust.loyalty_rank && cust.loyalty_rank !== 'NONE'
            ? `<span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;background:var(--surface-2);font-size:0.7rem;color:var(--text-muted);margin-left:0.35rem;">${esc(cust.loyalty_rank)}</span>`
            : '';

        const retHtml = cust.is_returning
            ? `<span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;background:rgba(16,185,129,0.12);color:var(--success);font-size:0.7rem;margin-left:0.35rem;">🔁 ลูกค้าเก่า (${cust.past_confirmed_count} ครั้ง)</span>`
            : `<span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;background:rgba(59,130,246,0.12);color:var(--accent);font-size:0.7rem;margin-left:0.35rem;">🆕 ลูกค้าใหม่</span>`;

        const bannedHtml = cust.is_banned
            ? `<span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;background:rgba(239,68,68,0.15);color:var(--error);font-size:0.7rem;margin-left:0.35rem;">🚫 ถูกแบน</span>` : '';

        const blockedHtml = cust.is_blocked_bot
            ? `<span style="display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;background:rgba(245,158,11,0.15);color:var(--warning);font-size:0.7rem;margin-left:0.35rem;">🤖 block บอท</span>` : '';

        body.innerHTML = `
            <div style="display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:1rem;">

                <!-- Left: Slip image -->
                <div>
                    <div style="background:var(--surface-2);border-radius:8px;padding:0.5rem;text-align:center;">
                        ${slipUrl
                            ? `<img src="${slipUrl}" alt="slip" style="max-width:100%;max-height:60vh;border-radius:6px;cursor:zoom-in;" onclick="window.open('${slipUrl}','_blank')">`
                            : `<div style="padding:3rem;color:var(--text-muted);">⚠️ ไม่มีรูปสลิป</div>`
                        }
                    </div>
                </div>

                <!-- Right: Details -->
                <div style="display:flex;flex-direction:column;gap:0.625rem;font-size:0.875rem;">

                    <!-- Status row -->
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        ${statusBadge}
                        <span style="font-size:0.78rem;color:var(--text-muted);">#${d.id}</span>
                    </div>

                    <!-- Amount -->
                    <div style="background:var(--surface-2);border-radius:8px;padding:0.625rem 0.875rem;">
                        <div style="font-size:0.7rem;color:var(--text-muted);">ยอดที่จ่าย</div>
                        <div style="font-size:1.5rem;font-weight:700;color:var(--primary);">฿${fmt(d.amount)}</div>
                        ${pkg.price && d.discount > 0 ? `
                            <div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.2rem;">
                                ราคาเต็ม ฿${fmt(pkg.price)}
                                <span style="color:var(--success);margin-left:0.5rem;">ส่วนลด ฿${fmt(d.discount)}</span>
                            </div>` : ''
                        }
                    </div>

                    <!-- Package -->
                    <div class="detail-panel" style="padding:0.625rem 0.875rem;">
                        <div style="font-size:0.7rem;color:var(--text-muted);">📦 แพ็กเกจ</div>
                        <div style="font-weight:600;">${esc(pkg.name || '?')}</div>
                        <div style="font-size:0.75rem;color:var(--text-muted);">${esc(pkg.tier || '')} · ${pkg.duration_days || 0} วัน</div>
                    </div>

                    ${promo ? `
                    <div class="detail-panel" style="padding:0.625rem 0.875rem;background:rgba(245,158,11,0.08);">
                        <div style="font-size:0.7rem;color:var(--warning);">🎁 โปรโมชั่น</div>
                        <div style="font-weight:600;">${esc(promo.name)}</div>
                        <div style="font-size:0.75rem;color:var(--text-muted);">฿${fmt(promo.normal_price || 0)} → ฿${fmt(promo.promo_price || 0)}</div>
                    </div>` : ''}

                    <!-- Customer -->
                    <div class="detail-panel" style="padding:0.625rem 0.875rem;">
                        <div style="font-size:0.7rem;color:var(--text-muted);">👤 ลูกค้า</div>
                        <div>
                            <span style="font-weight:600;">${esc((cust.first_name || '') + ' ' + (cust.last_name || ''))}</span>
                            ${rankBadge} ${retHtml} ${bannedHtml} ${blockedHtml}
                        </div>
                        <div style="font-size:0.75rem;color:var(--text-muted);font-family:var(--font-mono);">
                            ${cust.username ? '@' + esc(cust.username) : ''} tg:${cust.telegram_id || '?'}
                        </div>
                        ${cust.total_spent > 0 ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-top:0.2rem;">รวมจ่ายมา ฿${fmt(cust.total_spent)}</div>` : ''}
                        <div style="margin-top:0.4rem;">
                            <button class="btn btn-sm btn-outline" onclick="closeModal();showCustomer360(${cust.id})">👤 Customer 360</button>
                        </div>
                    </div>

                    <!-- Slip metadata -->
                    <div class="detail-panel" style="padding:0.625rem 0.875rem;">
                        <div style="font-size:0.7rem;color:var(--text-muted);">📄 ข้อมูลสลิป</div>
                        <div style="font-size:0.78rem;line-height:1.6;">
                            <div><b>ผู้โอน:</b> ${slip.sender_name ? esc(slip.sender_name) : '<span style="color:var(--text-muted);">— Slip2Go อ่านไม่ออก</span>'}</div>
                            ${slip.sender_bank_name ? `<div><b>ธนาคาร:</b> ${esc(slip.sender_bank_name)}</div>` : ''}
                            ${slip.sender_bank_account ? `<div><b>เลขบัญชี:</b> <code>${esc(slip.sender_bank_account)}</code></div>` : ''}
                            <div><b>Trans ref:</b> ${slip.trans_ref ? `<code style="font-size:0.7rem;">${esc(slip.trans_ref)}</code>` : '<span style="color:var(--text-muted);">—</span>'}</div>
                            <div><b>Method:</b> ${esc(d.method)}</div>
                        </div>
                    </div>

                    <!-- Retry status (if any) -->
                    ${retry ? `
                    <div class="detail-panel" style="padding:0.625rem 0.875rem;background:rgba(245,158,11,0.08);border-left:3px solid var(--warning);">
                        <div style="font-size:0.7rem;color:var(--warning);">🔄 Slip2Go Retry</div>
                        <div style="font-size:0.78rem;">
                            <b>${esc(retry.status)}</b> · ลอง ${retry.attempt}/${retry.max_attempts} ครั้ง
                            ${retry.last_error ? `<div style="margin-top:0.2rem;color:var(--error);">⚠️ ${esc(retry.last_error)}</div>` : ''}
                        </div>
                    </div>` : ''}

                    <!-- Verification info -->
                    ${d.verified_at ? `
                    <div class="detail-panel" style="padding:0.625rem 0.875rem;">
                        <div style="font-size:0.7rem;color:var(--text-muted);">✅ ตรวจสอบโดย</div>
                        <div style="font-size:0.78rem;">
                            ${d.auto_approved ? '🤖 Auto (Slip2Go)' : `${esc(d.verifier_name || 'admin')} (tg:${d.verified_by})`}
                            <span style="color:var(--text-muted);margin-left:0.4rem;">${fmtDateTime(d.verified_at)}</span>
                        </div>
                        ${d.reject_reason ? `<div style="color:var(--error);font-size:0.78rem;margin-top:0.2rem;">เหตุผล: ${esc(d.reject_reason)}</div>` : ''}
                    </div>` : ''}

                    <!-- Timestamps -->
                    <div style="font-size:0.7rem;color:var(--text-muted);text-align:right;">
                        ส่งสลิปเมื่อ ${fmtDateTime(d.created_at)}
                    </div>

                    <!-- Action buttons (only if pending) -->
                    ${d.status === 'PENDING' ? `
                    <div style="display:flex;gap:0.5rem;margin-top:0.5rem;">
                        <button class="btn btn-success" style="flex:1;" onclick="closeModal();inboxAction('approve_payment', ${d.id})">✅ Approve</button>
                        <button class="btn btn-danger" style="flex:1;" onclick="closeModal();inboxAction('reject_payment', ${d.id})">❌ Reject</button>
                    </div>` : ''}
                </div>
            </div>
        `;

        // Revoke blob URL when modal closes (memory)
        setTimeout(() => { try { if (slipUrl) URL.revokeObjectURL(slipUrl); } catch (e) {} }, 5 * 60 * 1000);
    } catch (err) {
        const body = document.getElementById('slip-modal-body');
        if (body) body.innerHTML = `<div style="color:var(--error);padding:1rem;">❌ ${esc(err.message || 'load failed')}</div>`;
        else toast(`❌ ${err.message || 'load failed'}`, 'error');
    }
}

// ===== Boss view: who bought today/yesterday + details =====
let _purchasesPeriod = 'today';

async function openPurchasesModal(period) {
    _purchasesPeriod = period || _purchasesPeriod || 'today';
    openModal('🛒 รายการออเดอร์', `
        <div id="purchases-body" style="text-align:center;padding:1rem;">
            <div class="spinner"></div>
            <div style="margin-top:0.5rem;color:var(--text-muted);font-size:0.875rem;">กำลังโหลด...</div>
        </div>
    `, { wide: true });
    await loadPurchases();
}

async function setPurchasesPeriod(period) {
    _purchasesPeriod = period;
    await loadPurchases();
}

async function loadPurchases() {
    const body = document.getElementById('purchases-body');
    if (!body) return;
    try {
        const r = await api(`/daily-report/purchases?period=${_purchasesPeriod}`);
        const s = r.summary;
        const items = r.items || [];

        const periodLabels = { today: 'วันนี้', yesterday: 'เมื่อวาน', week: '7 วัน', month: 'เดือนนี้' };

        // Filter chips
        const chips = ['today', 'yesterday', 'week', 'month'].map(p => `
            <button class="filter-btn ${_purchasesPeriod === p ? 'active' : ''}" onclick="setPurchasesPeriod('${p}')">${periodLabels[p]}</button>
        `).join('');

        // Summary cards
        const summaryHtml = `
            <div class="mini-cards" style="margin-bottom:0.75rem;">
                <div class="mini-card">
                    <div class="mini-card-label">รายได้</div>
                    <div class="mini-card-value" style="color:var(--primary);">${fmtBaht(s.revenue)}</div>
                </div>
                <div class="mini-card">
                    <div class="mini-card-label">สำเร็จ</div>
                    <div class="mini-card-value" style="color:var(--success);">${fmt(s.confirmed)}</div>
                </div>
                <div class="mini-card">
                    <div class="mini-card-label">รอตรวจ</div>
                    <div class="mini-card-value" style="color:var(--warning);">${fmt(s.pending)}</div>
                </div>
                <div class="mini-card">
                    <div class="mini-card-label">ลูกค้าซื้อ</div>
                    <div class="mini-card-value">${fmt(s.unique_buyers)} คน</div>
                </div>
            </div>
        `;

        if (items.length === 0) {
            body.innerHTML = `
                <div style="display:flex;gap:0.4rem;margin-bottom:0.75rem;flex-wrap:wrap;">${chips}</div>
                ${summaryHtml}
                <div style="text-align:center;padding:2rem;color:var(--text-muted);">ยังไม่มีออเดอร์ในช่วงนี้</div>
            `;
            return;
        }

        // Status icon
        const statusIcon = (st) => {
            if (st === 'CONFIRMED') return '<span style="color:var(--success);">✅</span>';
            if (st === 'PENDING') return '<span style="color:var(--warning);">⏳</span>';
            if (st === 'REJECTED') return '<span style="color:var(--error);">❌</span>';
            return st;
        };

        const rows = items.map(it => {
            const c = it.customer || {};
            const name = `${c.first_name || ''} ${c.last_name || ''}`.trim() || c.username || `tg:${c.telegram_id}`;
            const handle = c.username ? `@${esc(c.username)}` : `<code style="font-size:0.7rem;">tg:${c.telegram_id}</code>`;

            const badges = [];
            if (c.is_returning) badges.push(`<span style="background:rgba(16,185,129,0.12);color:var(--success);padding:0.1rem 0.4rem;border-radius:4px;font-size:0.65rem;">🔁 ${c.past_confirmed_count}</span>`);
            else badges.push(`<span style="background:rgba(59,130,246,0.12);color:var(--accent);padding:0.1rem 0.4rem;border-radius:4px;font-size:0.65rem;">🆕</span>`);
            if (c.loyalty_rank && c.loyalty_rank !== 'NONE') badges.push(`<span style="background:var(--surface-2);color:var(--text-muted);padding:0.1rem 0.4rem;border-radius:4px;font-size:0.65rem;">${esc(c.loyalty_rank)}</span>`);
            if (c.is_banned) badges.push(`<span style="background:rgba(239,68,68,0.15);color:var(--error);padding:0.1rem 0.4rem;border-radius:4px;font-size:0.65rem;">🚫</span>`);

            const promoInfo = it.promo_name ? ` <span style="color:var(--warning);font-size:0.7rem;">🎁 ${esc(it.promo_name)}</span>` : '';
            const discountInfo = it.discount > 0 ? ` <span style="color:var(--success);font-size:0.7rem;">-฿${fmt(it.discount)}</span>` : '';
            const autoTag = it.auto_approved ? ' <span style="font-size:0.65rem;color:var(--text-muted);">🤖 auto</span>' : '';

            return `
                <tr style="border-top:1px solid var(--border);" onclick="closeModal();openSlipImage(${it.id})" title="คลิกดูสลิป" class="purchase-row" style="cursor:pointer;">
                    <td style="padding:0.5rem;font-size:0.75rem;color:var(--text-muted);font-family:var(--font-mono);">${fmtTime(it.created_at)}</td>
                    <td style="padding:0.5rem;font-size:0.65rem;">${statusIcon(it.status)}</td>
                    <td style="padding:0.5rem;">
                        <div style="font-weight:500;">${esc(name)}</div>
                        <div style="font-size:0.7rem;color:var(--text-muted);">${handle} ${badges.join(' ')}</div>
                    </td>
                    <td style="padding:0.5rem;font-size:0.78rem;">
                        <div>${esc(it.package_name || '?')}</div>
                        <div style="font-size:0.68rem;color:var(--text-muted);">${esc(it.package_tier || '')} · ${it.duration_days || 0} วัน${promoInfo}</div>
                    </td>
                    <td style="padding:0.5rem;text-align:right;font-variant-numeric:tabular-nums;">
                        <div style="font-weight:600;">${fmtBaht(it.amount)}${discountInfo}</div>
                        <div style="font-size:0.65rem;color:var(--text-muted);">${esc(it.method)}${autoTag}</div>
                    </td>
                </tr>
            `;
        }).join('');

        body.innerHTML = `
            <div style="display:flex;gap:0.4rem;margin-bottom:0.75rem;flex-wrap:wrap;">${chips}</div>
            ${summaryHtml}
            <div class="table-wrap" style="max-height:60vh;overflow:auto;">
                <table style="width:100%;font-size:0.82rem;table-layout:auto;">
                    <thead style="background:var(--surface-2);position:sticky;top:0;">
                        <tr>
                            <th style="padding:0.5rem;text-align:left;width:60px;">เวลา</th>
                            <th style="padding:0.5rem;text-align:center;width:36px;"></th>
                            <th style="padding:0.5rem;text-align:left;">ลูกค้า</th>
                            <th style="padding:0.5rem;text-align:left;">แพ็กเกจ</th>
                            <th style="padding:0.5rem;text-align:right;">ยอด</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div style="margin-top:0.75rem;font-size:0.7rem;color:var(--text-muted);text-align:center;">
                💡 คลิกแถวเพื่อเปิดสลิป + รายละเอียดเต็ม · เฉลี่ย ฿${fmt(Math.round(s.avg_order))}/ออเดอร์
            </div>
        `;
    } catch (err) {
        body.innerHTML = `<div style="color:var(--error);padding:1rem;">❌ ${esc(err.message || 'load failed')}</div>`;
    }
}

// fmtTime helper (HH:MM only)
function fmtTime(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        return d.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch { return iso.slice(11, 16); }
}

// ===== Customer 360 — regen invite links + group memberships =====
async function regenInviteLinks(uid) {
    try {
        // Load options first
        const opts = await api(`/customers/${uid}/regen-link-options`);
        const groups = opts.groups || [];
        const customer = opts.customer || {};

        if (groups.length === 0) {
            toast('ลูกค้านี้ไม่มี VIP groups ที่จะส่งลิงก์ได้', 'error');
            return;
        }

        // Build modal
        const checkboxes = groups.map(g => `
            <label style="display:flex;gap:0.5rem;align-items:center;padding:0.5rem;border:1px solid var(--border);border-radius:6px;cursor:pointer;background:var(--surface-2);">
                <input type="checkbox" class="regen-slug-cb" data-slug="${esc(g.slug)}" data-title="${esc(g.title || g.slug)}" checked>
                <div style="flex:1;">
                    <div style="font-weight:600;font-size:0.85rem;">${esc(g.title || g.slug)}</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);">${esc(g.slug)} · ${esc(g.min_tier || '')}</div>
                </div>
            </label>
        `).join('');

        const fname = esc(customer.first_name || `ลูกค้า`);
        const pkgName = esc(opts.subscription?.package_name || '');
        const endDate = opts.subscription?.end_date ? fmtDate(opts.subscription.end_date) : '—';

        const defaultMsg = `🔄 <b>ลิงก์เข้ากลุ่มใหม่</b>
📦 แพ็กเกจ: ${pkgName}
📅 หมดอายุ: ${endDate}

👇 กดปุ่มด้านล่างเข้ากลุ่ม`;

        openModal(`🔄 ส่งลิงก์ใหม่ให้ ${fname}`, `
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(min(100%, 350px), 1fr));gap:1rem;">
                <!-- Left: Group picker + message editor -->
                <div>
                    <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.5rem;">
                        เลือกกลุ่มที่จะสร้างลิงก์ใหม่ (${groups.length} กลุ่ม)
                    </div>
                    <div style="display:flex;gap:0.4rem;margin-bottom:0.5rem;">
                        <button class="btn btn-sm btn-outline" onclick="regenToggleAll(true)" style="font-size:0.7rem;">✅ เลือกทุกกลุ่ม</button>
                        <button class="btn btn-sm btn-outline" onclick="regenToggleAll(false)" style="font-size:0.7rem;">⬜ ไม่เลือกเลย</button>
                    </div>
                    <div id="regen-groups-list" style="display:flex;flex-direction:column;gap:0.3rem;max-height:30vh;overflow:auto;margin-bottom:0.75rem;">
                        ${checkboxes}
                    </div>

                    <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.3rem;">
                        ข้อความ DM (HTML รองรับ)
                    </div>
                    <textarea id="regen-msg" rows="6" style="width:100%;font-size:0.78rem;font-family:var(--font-sans);padding:0.5rem;background:var(--surface);border:1px solid var(--border);border-radius:6px;color:var(--text);line-height:1.5;resize:vertical;" oninput="regenUpdatePreview()">${esc(defaultMsg)}</textarea>
                    <div style="display:flex;gap:0.3rem;margin-top:0.4rem;flex-wrap:wrap;">
                        <button class="btn btn-sm btn-outline" onclick="regenInsertTemplate('default')" style="font-size:0.7rem;">📋 Default</button>
                        <button class="btn btn-sm btn-outline" onclick="regenInsertTemplate('lost')" style="font-size:0.7rem;">😔 ลิงก์หาย</button>
                        <button class="btn btn-sm btn-outline" onclick="regenInsertTemplate('expired')" style="font-size:0.7rem;">⏰ ลิงก์หมดอายุ</button>
                        <button class="btn btn-sm btn-outline" onclick="regenInsertTemplate('blank')" style="font-size:0.7rem;">⚪ ว่าง</button>
                    </div>
                </div>

                <!-- Right: Live preview -->
                <div>
                    <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.5rem;">📱 ตัวอย่างใน Telegram</div>
                    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:0.875rem;">
                        <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.5rem;">
                            <div style="width:32px;height:32px;border-radius:50%;background:var(--primary);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:0.85rem;">P</div>
                            <div>
                                <div style="font-size:0.85rem;font-weight:600;">เจริญพร Bot</div>
                                <div style="font-size:0.7rem;color:var(--text-muted);">ตอนนี้</div>
                            </div>
                        </div>
                        <div id="regen-preview-text" style="font-size:0.875rem;line-height:1.6;white-space:pre-wrap;word-break:break-word;background:var(--surface-2);padding:0.625rem 0.75rem;border-radius:8px;"></div>
                        <div id="regen-preview-buttons" style="display:grid;grid-template-columns:1fr 1fr;gap:0.4rem;margin-top:0.5rem;"></div>
                    </div>
                </div>
            </div>

            <div style="display:flex;gap:0.5rem;margin-top:1rem;justify-content:flex-end;border-top:1px solid var(--border);padding-top:0.75rem;">
                <button class="btn btn-outline" onclick="closeModal()">ยกเลิก</button>
                <button class="btn btn-primary" onclick="doRegenLinks(${uid})">🚀 สร้างลิงก์ + ส่ง DM</button>
            </div>
        `, { wide: true });

        // Hook checkbox changes to update preview
        document.querySelectorAll('.regen-slug-cb').forEach(cb => {
            cb.addEventListener('change', regenUpdatePreview);
        });
        regenUpdatePreview();
    } catch (err) {
        toast('❌ ' + (err.message || 'load failed'), 'error');
    }
}

function regenToggleAll(checked) {
    document.querySelectorAll('.regen-slug-cb').forEach(cb => { cb.checked = checked; });
    regenUpdatePreview();
}

function regenUpdatePreview() {
    const msg = document.getElementById('regen-msg')?.value || '';
    const txt = document.getElementById('regen-preview-text');
    const btns = document.getElementById('regen-preview-buttons');
    if (!txt || !btns) return;

    // Render HTML preview
    txt.innerHTML = msg;

    const selected = Array.from(document.querySelectorAll('.regen-slug-cb'))
        .filter(cb => cb.checked)
        .map(cb => ({ slug: cb.dataset.slug, title: cb.dataset.title }));

    btns.innerHTML = selected.map(g => `
        <button style="padding:0.4rem 0.6rem;background:var(--accent);color:#fff;border-radius:6px;border:none;font-size:0.75rem;cursor:pointer;">🚀 ${esc(g.title)}</button>
    `).join('');
}

function regenInsertTemplate(type) {
    const ta = document.getElementById('regen-msg');
    if (!ta) return;
    const templates = {
        default: `🔄 <b>ลิงก์เข้ากลุ่มใหม่</b>
📦 กดปุ่มด้านล่างเข้ากลุ่มได้เลย

💡 ลิงก์นี้ใช้ได้ครั้งเดียวค่ะ`,
        lost: `❤️ <b>ลิงก์เข้ากลุ่มใหม่</b>

เห็นว่าน้องยังไม่ได้กดเข้ากลุ่มเลยส่งลิงก์ใหม่ให้นะคะ

👇 กดปุ่มด้านล่างเข้าได้เลย`,
        expired: `⏰ <b>ลิงก์เก่าหมดอายุแล้วค่ะ</b>

ส่งลิงก์ใหม่ให้นะคะ — รีบกดก่อนหมดอีกนะ 😊

👇 กดเลย`,
        blank: ``,
    };
    ta.value = templates[type] || '';
    regenUpdatePreview();
}

async function doRegenLinks(uid) {
    const slugs = Array.from(document.querySelectorAll('.regen-slug-cb'))
        .filter(cb => cb.checked)
        .map(cb => cb.dataset.slug);
    const message = document.getElementById('regen-msg')?.value || '';
    if (slugs.length === 0) { toast('เลือกอย่างน้อย 1 กลุ่ม', 'error'); return; }
    if (!message.trim()) { toast('ข้อความว่าง', 'error'); return; }

    try {
        const r = await api(`/customers/${uid}/regen-links`, {
            method: 'POST',
            body: JSON.stringify({ slugs, message }),
        });
        if (r.dm_sent) {
            toast(`✅ สร้าง ${r.links.length} ลิงก์ + DM แล้ว`, 'success');
        } else {
            toast(`⚠️ ลิงก์สร้างแล้ว — DM ไม่ส่ง: ${r.dm_error || 'unknown'}`, 'error', 10000);
        }
        // Show result modal with links
        const linksHtml = r.links.map(l => `
            <div style="padding:0.5rem;border:1px solid var(--border);border-radius:6px;margin-bottom:0.4rem;">
                <div style="font-weight:600;font-size:0.85rem;">${esc(l.title)} <code style="font-size:0.7rem;color:var(--text-muted);">${esc(l.slug)}</code></div>
                <div style="display:flex;gap:0.4rem;align-items:center;margin-top:0.3rem;">
                    <input value="${esc(l.url)}" readonly style="flex:1;font-size:0.75rem;font-family:var(--font-mono);">
                    <button class="btn btn-sm btn-outline" onclick="navigator.clipboard.writeText('${esc(l.url)}');toast('คัดลอกแล้ว','success');">📋</button>
                </div>
            </div>
        `).join('');
        closeModal();
        setTimeout(() => {
            openModal(`🔄 ลิงก์ใหม่ (${r.links.length})`, `
                <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.5rem;">
                    ${r.dm_sent ? '✅ ส่ง DM แล้ว' : '⚠️ DM ไม่ส่ง — copy ส่งให้ลูกค้าเอง'}
                </div>
                ${linksHtml}
            `);
        }, 200);
    } catch (err) {
        toast('❌ ' + (err.message || 'regen failed'), 'error');
    }
}

async function loadCustomerGroups(uid) {
    const el = document.getElementById(`c360-groups-${uid}`);
    if (!el) return;
    el.innerHTML = '<div class="spinner" style="margin:1rem auto;"></div>';
    try {
        const r = await api(`/customers/${uid}/group-memberships`);
        const renderGroup = (g) => `
            <span style="display:inline-flex;align-items:center;gap:0.3rem;padding:0.2rem 0.5rem;border-radius:4px;background:var(--surface-2);font-size:0.72rem;margin:0.15rem;">
                ${esc(g.slug)}
                ${g.title ? `<span style="color:var(--text-muted);">${esc(g.title)}</span>` : ''}
            </span>`;

        let html = '';
        if (r.vip_in.length > 0) {
            html += `<div style="margin-bottom:0.5rem;">
                <div style="font-size:0.72rem;color:var(--success);font-weight:600;margin-bottom:0.25rem;">✅ VIP ที่อยู่ (${r.vip_in.length})</div>
                <div>${r.vip_in.map(renderGroup).join('')}</div>
            </div>`;
        }
        if (r.vip_missing.length > 0) {
            html += `<div style="margin-bottom:0.5rem;">
                <div style="font-size:0.72rem;color:var(--error);font-weight:600;margin-bottom:0.25rem;">⚠️ VIP ที่ขาด (${r.vip_missing.length}) — ควรอยู่แต่ไม่อยู่</div>
                <div>${r.vip_missing.map(renderGroup).join('')}</div>
            </div>`;
        }
        if (r.free_in.length > 0) {
            html += `<div style="margin-bottom:0.5rem;">
                <div style="font-size:0.72rem;color:var(--accent);font-weight:600;margin-bottom:0.25rem;">🆓 กลุ่มฟรี (${r.free_in.length})</div>
                <div>${r.free_in.map(renderGroup).join('')}</div>
            </div>`;
        }
        if (r.total_groups_in === 0) {
            html = '<div style="color:var(--text-muted);font-size:0.78rem;text-align:center;padding:0.5rem;">ยังไม่อยู่กลุ่มไหนเลย</div>';
        }
        el.innerHTML = html;
    } catch (err) {
        el.innerHTML = `<div style="color:var(--error);font-size:0.78rem;">${esc(err.message || 'check failed')}</div>`;
    }
}

// ===== Group Broadcast — admin self-serve via dashboard =====
let _gbSelectedSlugs = new Set();
let _gbImageFile = null;

async function openGroupBroadcastModal() {
    openModal('📣 บรอดแคสต์ลงกลุ่ม', `
        <div id="gb-body" style="text-align:center;padding:1rem;">
            <div class="spinner"></div>
        </div>
    `, { wide: true });

    try {
        const data = await api('/group-broadcast/groups');
        const free = data.free || [];
        const vip = data.vip || [];

        _gbSelectedSlugs = new Set();
        _gbImageFile = null;

        const renderGroup = (g) => `
            <label style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0.5rem;border:1px solid var(--border);border-radius:6px;cursor:pointer;background:var(--surface-2);">
                <input type="checkbox" class="gb-cb" data-slug="${esc(g.slug)}" data-title="${esc(g.title || g.slug)}" style="width:16px;height:16px;" onchange="gbToggle('${esc(g.slug)}')">
                <div style="flex:1;min-width:0;">
                    <div style="font-weight:500;font-size:0.78rem;line-height:1.2;">${esc(g.title || g.slug)}</div>
                    <div style="font-size:0.65rem;color:var(--text-muted);">${esc(g.slug)}</div>
                </div>
            </label>
        `;

        document.getElementById('gb-body').innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(min(100%, 380px), 1fr));gap:1rem;">

                <!-- LEFT: Group picker -->
                <div>
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
                        <strong style="font-size:0.85rem;">เลือกกลุ่มเป้าหมาย <span id="gb-count" style="color:var(--text-muted);font-weight:normal;">(0)</span></strong>
                    </div>
                    <div style="display:flex;gap:0.3rem;margin-bottom:0.5rem;flex-wrap:wrap;">
                        <button class="btn btn-sm btn-outline" onclick="gbSelectAll('all')" style="font-size:0.7rem;">เลือกทั้งหมด</button>
                        <button class="btn btn-sm btn-outline" onclick="gbSelectAll('free')" style="font-size:0.7rem;">เลือกฟรีทั้งหมด (${free.length})</button>
                        <button class="btn btn-sm btn-outline" onclick="gbSelectAll('vip')" style="font-size:0.7rem;">เลือก VIP (${vip.length})</button>
                        <button class="btn btn-sm btn-outline" onclick="gbSelectAll('none')" style="font-size:0.7rem;">ล้าง</button>
                    </div>

                    ${vip.length > 0 ? `
                    <div style="font-size:0.72rem;color:var(--warning);font-weight:600;margin:0.5rem 0 0.25rem;">👑 VIP (${vip.length})</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.3rem;margin-bottom:0.5rem;">
                        ${vip.map(renderGroup).join('')}
                    </div>
                    ` : ''}

                    ${free.length > 0 ? `
                    <div style="font-size:0.72rem;color:var(--accent);font-weight:600;margin:0.5rem 0 0.25rem;">🆓 ฟรี (${free.length})</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.3rem;max-height:35vh;overflow:auto;">
                        ${free.map(renderGroup).join('')}
                    </div>
                    ` : ''}
                </div>

                <!-- RIGHT: Compose -->
                <div>
                    <strong style="font-size:0.85rem;">เขียนข้อความ</strong>
                    <div class="ct-toolbar" style="display:flex;gap:0.25rem;flex-wrap:wrap;margin-top:0.3rem;padding:0.35rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px 6px 0 0;border-bottom:none;">
                        <button type="button" onclick="gbFormat('"'"'b'"'"')" title="ตัวหนา" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;"><b>B</b></button>
                        <button type="button" onclick="gbFormat('"'"'i'"'"')" title="ตัวเอียง" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;"><i>I</i></button>
                        <button type="button" onclick="gbFormat('"'"'u'"'"')" title="ขีดเส้นใต้" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;"><u>U</u></button>
                        <button type="button" onclick="gbFormat('"'"'s'"'"')" title="ขีดทับ" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;"><s>S</s></button>
                        <span style="color:var(--text-dim);align-self:center;">|</span>
                        <button type="button" onclick="gbInsertLink()" title="ลิ้ง" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;">🔗 ลิ้ง</button>
                        <button type="button" onclick="gbInsertDivider()" title="เส้นคั่น" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;">〰️ เส้นคั่น</button>
                        <button type="button" onclick="gbOpenEmoji(this)" title="อีโมจิ" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:0.25rem 0.55rem;cursor:pointer;font-size:0.78rem;">😊 อีโมจิ</button>
                    </div>
                    <textarea id="gb-msg" rows="14" placeholder="พิมพ์ข้อความที่จะส่ง... รองรับ HTML เช่น <b>หนา</b>, <i>เอียง</i>, <a href='url'>ลิงก์</a>" style="width:100%;font-family:var(--font-sans);font-size:0.85rem;padding:0.625rem;background:var(--surface);border:1px solid var(--border);border-radius:0 0 6px 6px;color:var(--text);line-height:1.5;resize:vertical;margin-top:0;" oninput="gbUpdatePreview()"></textarea>

                    <div style="display:flex;gap:0.4rem;align-items:center;margin-top:0.5rem;flex-wrap:wrap;">
                        <label style="font-size:0.72rem;display:flex;align-items:center;gap:0.3rem;cursor:pointer;">
                            <input type="file" id="gb-image" accept="image/*" style="display:none;" onchange="gbImageChange(this)">
                            <span class="btn btn-sm btn-outline" onclick="document.getElementById('gb-image').click()">🖼 เลือกรูป</span>
                        </label>
                        <span id="gb-image-name" style="font-size:0.7rem;color:var(--text-muted);"></span>
                        <span id="gb-image-clear" style="display:none;font-size:0.7rem;color:var(--error);cursor:pointer;" onclick="gbClearImage()">✕ ลบรูป</span>
                    </div>

                    <div style="margin-top:1rem;font-size:0.72rem;color:var(--text-muted);">📱 ตัวอย่าง</div>
                    <div id="gb-preview" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.625rem;font-size:0.85rem;line-height:1.5;min-height:80px;max-height:25vh;overflow:auto;white-space:pre-wrap;word-break:break-word;color:var(--text-muted);">— ยังไม่มีข้อความ —</div>

                    <div style="margin-top:1rem;display:flex;gap:0.5rem;justify-content:flex-end;border-top:1px solid var(--border);padding-top:0.75rem;">
                        <button class="btn btn-outline" onclick="closeModal()">ยกเลิก</button>
                        <button class="btn btn-primary" onclick="doGroupBroadcast()" id="gb-send-btn">📣 ส่งบรอดแคสต์</button>
                    </div>
                </div>
            </div>

            <div style="margin-top:1rem;padding-top:0.75rem;border-top:1px solid var(--border);">
                <button class="btn btn-sm btn-outline" onclick="showGbHistory()">📜 ดูประวัติบรอดแคสต์</button>
            </div>
        `;
        gbUpdatePreview();
    } catch (err) {
        document.getElementById('gb-body').innerHTML = `<div style="color:var(--error);padding:1rem;">❌ ${esc(err.message || 'load failed')}</div>`;
    }
}

function gbToggle(slug) {
    if (_gbSelectedSlugs.has(slug)) _gbSelectedSlugs.delete(slug);
    else _gbSelectedSlugs.add(slug);
    document.getElementById('gb-count').textContent = `(${_gbSelectedSlugs.size})`;
}

function gbSelectAll(mode) {
    _gbSelectedSlugs = new Set();
    document.querySelectorAll('.gb-cb').forEach(cb => {
        const slug = cb.dataset.slug;
        const isFree = slug.startsWith('FREE');
        const shouldCheck = (mode === 'all') || (mode === 'free' && isFree) || (mode === 'vip' && !isFree);
        cb.checked = shouldCheck;
        if (shouldCheck) _gbSelectedSlugs.add(slug);
    });
    document.getElementById('gb-count').textContent = `(${_gbSelectedSlugs.size})`;
}

function gbImageChange(input) {
    if (input.files && input.files[0]) {
        _gbImageFile = input.files[0];
        document.getElementById('gb-image-name').textContent = _gbImageFile.name + ` (${Math.round(_gbImageFile.size/1024)}KB)`;
        document.getElementById('gb-image-clear').style.display = 'inline';
    }
}

function gbClearImage() {
    _gbImageFile = null;
    document.getElementById('gb-image').value = '';
    document.getElementById('gb-image-name').textContent = '';
    document.getElementById('gb-image-clear').style.display = 'none';
}


// ===== Broadcast modal: rich text helpers (reuse CT_EMOJIS + ctInsertAt) =====
function _gbTA() { return document.getElementById("gb-msg"); }
function gbFormat(tag) {
    const ta = _gbTA(); if (!ta) return;
    ctInsertAt(ta, "<" + tag + ">", "</" + tag + ">");
    gbUpdatePreview();
}
function gbInsertText(text) {
    const ta = _gbTA(); if (!ta) return;
    ctInsertAt(ta, text, "");
    gbUpdatePreview();
}
function gbInsertLink() {
    const url = prompt("ใส่ URL ลิ้ง:", "https://t.me/NamwarnJarern_bot");
    if (!url) return;
    const label = prompt("ข้อความที่ลูกค้าเห็น:", "กดที่นี่") || url;
    const ta = _gbTA(); if (!ta) return;
    ctInsertAt(ta, "<a href=\"" + url + "\">" + label + "</a>", "");
    gbUpdatePreview();
}
function gbInsertDivider() {
    gbInsertText("\n━━━━━━━━━━━━━━━━━━\n");
}
function gbOpenEmoji(btn) {
    document.querySelectorAll(".ct-emoji-popover, .gb-emoji-popover").forEach(p => p.remove());
    const ta = _gbTA(); if (!ta) return;
    const pop = document.createElement("div");
    pop.className = "gb-emoji-popover";
    pop.style.cssText = "position:absolute;background:#27272a;border:1px solid #3f3f46;border-radius:8px;padding:0.4rem;display:grid;grid-template-columns:repeat(8,1fr);gap:0.2rem;z-index:100001;box-shadow:0 4px 16px rgba(0,0,0,0.5);";
    CT_EMOJIS.forEach(emoji => {
        const b = document.createElement("button");
        b.textContent = emoji;
        b.type = "button";
        b.style.cssText = "background:transparent;border:none;font-size:1.2rem;cursor:pointer;padding:0.25rem;border-radius:4px;color:#fff;";
        b.onmouseover = () => b.style.background = "#3f3f46";
        b.onmouseout = () => b.style.background = "transparent";
        b.onclick = (e) => {
            e.stopPropagation();
            ctInsertAt(ta, emoji, "");
            gbUpdatePreview();
            pop.remove();
        };
        pop.appendChild(b);
    });
    const r = btn.getBoundingClientRect();
    pop.style.top = (r.bottom + window.scrollY + 4) + "px";
    pop.style.left = (r.left + window.scrollX) + "px";
    document.body.appendChild(pop);
    setTimeout(() => {
        const cls = (e) => {
            if (!pop.contains(e.target) && e.target !== btn) {
                pop.remove();
                document.removeEventListener("click", cls);
            }
        };
        document.addEventListener("click", cls);
    }, 100);
}

function gbUpdatePreview() {
    const msg = document.getElementById('gb-msg')?.value || '';
    const el = document.getElementById('gb-preview');
    if (!el) return;
    if (!msg.trim()) {
        el.innerHTML = '<span style="color:var(--text-dim);">— ยังไม่มีข้อความ —</span>';
        el.style.color = 'var(--text-muted)';
    } else {
        el.innerHTML = msg;
        el.style.color = 'var(--text)';
    }
}

async function doGroupBroadcast() {
    const slugs = Array.from(_gbSelectedSlugs);
    const msg = document.getElementById('gb-msg').value.trim();

    if (slugs.length === 0) { toast('เลือกอย่างน้อย 1 กลุ่ม', 'error'); return; }
    if (!msg && !_gbImageFile) { toast('ใส่ข้อความหรือรูป', 'error'); return; }
    if (!await confirmModal({ message: `ส่งบรอดแคสต์ไปยัง ${slugs.length} กลุ่ม?\n\nอาจใช้เวลา ${Math.round(slugs.length * 0.6)} วินาที`, dangerous: true })) return;

    const btn = document.getElementById('gb-send-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ กำลังส่ง...'; }

    try {
        const form = new FormData();
        form.append('slugs', JSON.stringify(slugs));
        form.append('message', msg);
        form.append('parse_mode', 'HTML');
        if (_gbImageFile) form.append('image', _gbImageFile);

        const resp = await fetch('/api/group-broadcast/send', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: form,
        });
        const r = await resp.json();
        if (!resp.ok) {
            throw new Error(r.detail || 'send failed');
        }

        if (r.failed === 0) {
            toast(`✅ ส่งสำเร็จทุกกลุ่ม (${r.sent}/${r.total})`, 'success');
            closeModal();
        } else {
            const errLines = (r.errors || []).map(e => `• ${e.slug}: ${e.error}`).join('\n');
            toast(`⚠️ ส่งสำเร็จ ${r.sent} / ล้มเหลว ${r.failed}\n${errLines}`.slice(0, 300), 'error', 15000);
            // Re-enable button
            if (btn) { btn.disabled = false; btn.textContent = '📣 ส่งบรอดแคสต์'; }
        }
    } catch (err) {
        toast(`❌ ${err.message || 'failed'}`, 'error');
        if (btn) { btn.disabled = false; btn.textContent = '📣 ส่งบรอดแคสต์'; }
    }
}

async function showGbHistory() {
    try {
        const items = await api('/group-broadcast/history?limit=30');
        const rows = items.map(it => {
            const slugs = (Array.isArray(it.target_slugs) ? it.target_slugs : JSON.parse(it.target_slugs || '[]')).join(', ');
            const okIcon = it.failed_count === 0 ? '✅' : '⚠️';
            return `
                <tr style="border-top:1px solid var(--border);">
                    <td style="padding:0.4rem;font-size:0.7rem;color:var(--text-muted);">${fmtDateTime(it.sent_at)}</td>
                    <td style="padding:0.4rem;font-size:0.72rem;">${esc(it.admin_name || '?')}</td>
                    <td style="padding:0.4rem;font-size:0.7rem;color:var(--text-muted);">${esc(slugs.slice(0,80))}${slugs.length>80?'…':''}</td>
                    <td style="padding:0.4rem;text-align:right;">${okIcon} ${it.sent_count}${it.failed_count>0?'/'+it.failed_count+' ❌':''}</td>
                    <td style="padding:0.4rem;font-size:0.7rem;">${it.has_image ? '🖼' : ''} ${esc(it.preview).slice(0,60)}…</td>
                </tr>`;
        }).join('');
        openModal('📜 ประวัติบรอดแคสต์', `
            <div style="max-height:70vh;overflow:auto;">
                <table style="width:100%;font-size:0.78rem;">
                    <thead style="background:var(--surface-2);">
                        <tr>
                            <th style="padding:0.4rem;text-align:left;">เวลา</th>
                            <th style="padding:0.4rem;text-align:left;">โดย</th>
                            <th style="padding:0.4rem;text-align:left;">กลุ่ม</th>
                            <th style="padding:0.4rem;text-align:right;">ผล</th>
                            <th style="padding:0.4rem;text-align:left;">ข้อความ</th>
                        </tr>
                    </thead>
                    <tbody>${rows || '<tr><td colspan="5" style="text-align:center;padding:1rem;color:var(--text-muted);">ยังไม่มีประวัติ</td></tr>'}</tbody>
                </table>
            </div>
        `, { wide: true });
    } catch (err) {
        toast(`❌ ${err.message || 'load failed'}`, 'error');
    }
}

// ===== Send DM directly from Prae Log modal =====
async function sendPraeDM(uid, telegramId) {
    const input = document.getElementById(`prae-dm-input-${uid}`);
    const btn = document.getElementById(`prae-dm-send-${uid}`);
    if (!input) return;
    const msg = input.value.trim();
    if (!msg) { toast('พิมพ์ข้อความก่อน', 'error'); return; }
    if (msg.length > 4000) { toast('ข้อความยาวเกิน 4000 ตัว', 'error'); return; }

    btn.disabled = true;
    btn.textContent = '⏳';
    try {
        const r = await api(`/customers/${uid}/dm`, {
            method: 'POST',
            body: JSON.stringify({ message: msg }),
        });
        if (r.ok) {
            toast('✅ ส่ง DM แล้ว', 'success');
            input.value = '';
            // Append the new admin message to bubble area visually
            const bubbleArea = document.getElementById(`prae-bubbles-${telegramId}`);
            if (bubbleArea) {
                const ts = new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit', hour12: false });
                const bubbleHtml = `
                    <div style="display:flex;justify-content:flex-end;margin-bottom:0.5rem;">
                        <div style="max-width:75%;padding:0.5rem 0.75rem;border-radius:12px;background:var(--warning);color:#fff;font-size:0.875rem;">
                            <div style="font-size:0.7rem;opacity:0.85;margin-bottom:0.2rem;">👮 Admin · ${ts}</div>
                            <div style="white-space:pre-wrap;word-break:break-word;">${esc(msg)}</div>
                        </div>
                    </div>
                `;
                bubbleArea.insertAdjacentHTML('beforeend', bubbleHtml);
                bubbleArea.scrollTop = bubbleArea.scrollHeight;
            }
        } else {
            toast('❌ ส่งไม่สำเร็จ', 'error');
        }
    } catch (err) {
        toast('❌ ' + (err.message || 'ส่งไม่สำเร็จ'), 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '📤 ส่ง';
    }
}

// ===== Gacha: spin pricing + add prize =====
async function showGachaPricingModal() {
    try {
        const rows = await api('/gacha-admin/spin-pricing');
        const inputs = rows.map(r => `
            <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.5rem;">
                <code style="min-width:80px;font-weight:600;">${esc(r.tier)}</code>
                <span style="color:var(--text-muted);">฿</span>
                <input type="number" id="gp-${esc(r.tier)}" value="${r.price_thb}" min="1" max="99999" style="width:120px;">
                <button class="btn btn-sm btn-primary" onclick="saveGachaPrice('${esc(r.tier)}')">💾 บันทึก</button>
                <small style="color:var(--text-muted);">${r.updated_at ? fmtDateTime(r.updated_at) : ''}</small>
            </div>
        `).join('');
        openModal('💰 ราคา/หมุน Gacha', `
            <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.75rem;">
                แก้ราคาที่ลูกค้าจ่ายต่อบันเดิ้ลกาชา (sales bot อ่านจาก DB ทันที — ไม่ต้อง deploy)
            </div>
            ${inputs}
            <div style="margin-top:0.75rem;padding:0.5rem;background:var(--surface-2);border-radius:6px;font-size:0.7rem;color:var(--text-muted);">
                💡 ราคาเหล่านี้แทนที่ค่า hardcode ใน pricing.py · cache 60 วินาที
            </div>
        `);
    } catch (err) {
        toast('❌ ' + (err.message || 'load failed'), 'error');
    }
}

async function saveGachaPrice(tier) {
    const v = parseInt(document.getElementById(`gp-${tier}`).value);
    if (isNaN(v) || v < 1) { toast('ตัวเลข ≥ 1', 'error'); return; }
    try {
        await api(`/gacha-admin/spin-pricing/${tier}`, {
            method: 'PATCH', body: JSON.stringify({ price_thb: v }),
        });
        toast(`✅ ${tier} = ฿${v}`, 'success');
    } catch (err) {
        toast('❌ ' + (err.message || 'save failed'), 'error');
    }
}

async function showAddPrizeModal() {
    openModal('🎁 เพิ่มรางวัล Gacha', `
        <div class="form-group"><label>Code (uniq)</label><input id="ap-code" placeholder="เช่น COIN_50"></div>
        <div class="form-group"><label>ชื่อรางวัล</label><input id="ap-name" placeholder="เช่น เครดิตส่วนลด ฿50"></div>
        <div class="form-row">
            <div class="form-group"><label>Tier</label>
                <select id="ap-tier">
                    <option value="COMMON">COMMON</option>
                    <option value="RARE">RARE</option>
                    <option value="EPIC">EPIC</option>
                    <option value="LEGENDARY">LEGENDARY</option>
                </select>
            </div>
            <div class="form-group"><label>Type</label>
                <select id="ap-type">
                    <option value="discount">discount (เครดิตส่วนลด)</option>
                    <option value="clip">clip (ชุดคลิป)</option>
                    <option value="sub">sub (เปิดสิทธิ)</option>
                    <option value="cash">cash</option>
                    <option value="item">item</option>
                </select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>Value (฿)</label><input id="ap-value" type="number" value="50"></div>
            <div class="form-group"><label>Probability (%)</label><input id="ap-prob" type="number" step="0.001" value="5"></div>
        </div>
        <button class="btn btn-primary btn-full" onclick="doAddPrize()">💾 เพิ่มรางวัล</button>
    `);
}

async function doAddPrize() {
    try {
        const body = {
            code: document.getElementById('ap-code').value.trim(),
            name: document.getElementById('ap-name').value.trim(),
            tier: document.getElementById('ap-tier').value,
            prize_type: document.getElementById('ap-type').value,
            value_thb: parseFloat(document.getElementById('ap-value').value || 0),
            probability_pct: parseFloat(document.getElementById('ap-prob').value || 0),
            enabled: true,
            sort_order: 100,
        };
        if (!body.code || !body.name) { toast('กรอก code + name', 'error'); return; }
        await api('/gacha-admin/prize-pool', { method: 'POST', body: JSON.stringify(body) });
        toast('✅ เพิ่มรางวัลเรียบร้อย', 'success');
        closeModal();
        if (typeof renderGacha === 'function') renderGacha();
    } catch (err) {
        toast('❌ ' + (err.message || 'add failed'), 'error');
    }
}

async function deletePrize(pid, name) {
    if (!await confirmModal({ message: `ลบรางวัล "${name}"?\n\n(ถ้าเคยมีคนได้รางวัลนี้ จะ soft-delete = disabled ไม่ลบจริง)`, dangerous: true })) return;
    try {
        const r = await api(`/gacha-admin/prize-pool/${pid}`, { method: 'DELETE' });
        if (r.soft_deleted) toast(`✅ Disabled (มีคนได้ ${r.winners} ครั้ง — เก็บประวัติ)`, 'success');
        else toast('✅ ลบเรียบร้อย', 'success');
        if (typeof renderGacha === 'function') renderGacha();
    } catch (err) {
        toast('❌ ' + (err.message || 'delete failed'), 'error');
    }
}

// ===== Admin Telegram IDs — add delete button =====
async function deleteAdminId(tid) {
    if (!await confirmModal({ message: `ลบ Telegram ID ${tid}?\n\nบอททั้ง 4 ตัว (admin/content/sales/guardian) จะ restart`, dangerous: true })) return;
    try {
        const r = await api(`/admin/admin-ids/${tid}`, { method: 'DELETE' });
        toast(`✅ ลบ ${tid} เรียบร้อย (restart: ${Object.keys(r.restarts || {}).filter(k => r.restarts[k].ok).join(', ')})`, 'success');
        if (typeof renderBotManage === 'function') renderBotManage();
    } catch (err) {
        toast('❌ ' + (err.message || 'delete failed'), 'error');
    }
}

// ==================================================================
// Phase A.1b (2026-06-26): Today homepage + Feature Flags + Bot Messages
// ==================================================================

async function renderToday() {
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const [summary, alerts] = await Promise.all([
            api('/dashboard/summary').catch(() => ({})),
            api('/dashboard/alerts').catch(() => ({ items: [] })),
        ]);
        const tier = (summary.tier_breakdown || []);
        const todayRev = summary.today ?? 0;
        const todayChange = summary.today_change ?? 0;
        const pendingSlips = alerts.pending_slips ?? 0;
        const sosCount = alerts.sos_count ?? 0;
        const expToday = alerts.expiring_today ?? 0;
        const weekRev = summary.week ?? 0;
        const monthRev = summary.month ?? 0;
        const anomalies = (alerts.anomalies || []).length;

        const now = new Date();
        const days = ['อาทิตย์','จันทร์','อังคาร','พุธ','พฤหัส','ศุกร์','เสาร์'];
        const months = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
        const greet = now.getHours() < 12 ? 'อรุณสวัสดิ์' : (now.getHours() < 18 ? 'สวัสดีตอนบ่าย' : 'สวัสดีตอนเย็น');
        const dateStr = `${days[now.getDay()]}ที่ ${now.getDate()} ${months[now.getMonth()]} ${now.getFullYear() + 543}`;
        const timeStr = now.toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit'});

        let priorityHtml = '';
        if (pendingSlips > 0) {
            priorityHtml += `<div class="priority-card urgent" onclick="navigate('inbox')">
                <div class="pri-dot"></div>
                <div class="pri-body"><div class="pri-title">รออนุมัติสลิป ${pendingSlips} ใบ</div><div class="pri-meta">คลิกไปที่ Inbox</div></div>
                <div class="pri-cta">ไปที่ Inbox →</div>
            </div>`;
        }
        if (sosCount > 0) {
            priorityHtml += `<div class="priority-card warn" onclick="navigate('customers')">
                <div class="pri-dot"></div>
                <div class="pri-body"><div class="pri-title">SOS รอจัดการ ${sosCount} อัน</div><div class="pri-meta">ลูกค้าต้องการความช่วยเหลือ</div></div>
                <div class="pri-cta">ไปดู →</div>
            </div>`;
        }
        priorityHtml += `<div class="priority-card ok">
            <div class="pri-dot"></div>
            <div class="pri-body"><div class="pri-title">วันนี้: ${expToday} ลูกค้าจะหมดอายุ</div><div class="pri-meta">ระบบเตือนต่ออายุจะส่ง DM อัตโนมัติ</div></div>
            <div class="pri-cta">ตั้งค่า →</div>
        </div>`;

        if (!priorityHtml.includes('urgent') && !priorityHtml.includes('warn')) {
            priorityHtml = '<div class="priority-card ok"><div class="pri-dot"></div><div class="pri-body"><div class="pri-title">ทุกอย่างเรียบร้อย ✨</div><div class="pri-meta">ไม่มีงานเร่งด่วน</div></div></div>' + priorityHtml;
        }

        content.innerHTML = `
            <style>
                .today-hero { display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1.5rem; }
                .today-hero h1 { font-size:1.65rem;font-weight:700;margin-bottom:0.2rem;letter-spacing:-0.02em; }
                .today-hero .date { color:var(--text-dim);font-size:0.9rem; }
                .section-label { font-size:0.7rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-dim);font-weight:700;margin:1.5rem 0 0.6rem; }
                .priority-list { display:flex;flex-direction:column;gap:0.6rem; }
                .priority-card { background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:0.85rem 1.1rem;display:flex;align-items:center;gap:1rem;cursor:pointer;transition:all 0.15s;box-shadow:var(--shadow-sm); }
                .priority-card:hover { transform:translateY(-1px);border-color:var(--border-strong); }
                .priority-card.urgent { border-left:3px solid var(--error); }
                .priority-card.urgent .pri-dot { background:var(--error); }
                .priority-card.warn { border-left:3px solid var(--warning); }
                .priority-card.warn .pri-dot { background:var(--warning); }
                .priority-card.ok { border-left:3px solid var(--success); }
                .priority-card.ok .pri-dot { background:var(--success); }
                .pri-dot { width:12px;height:12px;border-radius:50%;flex-shrink:0; }
                .pri-body { flex:1; }
                .pri-title { font-weight:600;font-size:0.95rem; }
                .pri-meta { color:var(--text-muted);font-size:0.8rem;margin-top:0.1rem; }
                .pri-cta { color:var(--accent);font-weight:600;font-size:0.8rem; }
                .qa-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:0.6rem; }
                .qa-btn { background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:0.85rem 1rem;cursor:pointer;font-weight:500;font-size:0.875rem;color:var(--text);text-align:left;font-family:inherit;display:flex;align-items:center;gap:0.5rem; }
                .qa-btn:hover { background:var(--surface-2);border-color:var(--border-strong); }
                .mini-stats { display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.75rem;margin-bottom:1rem; }
                .mini-stat { background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:1rem 1.1rem; }
                .mini-stat .ms-label { font-size:0.7rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.05em;font-weight:600;margin-bottom:0.3rem; }
                .mini-stat .ms-val { font-size:1.5rem;font-weight:700;letter-spacing:-0.02em; }
            </style>
            <div class="today-hero">
                <div>
                    <h1>${greet} 👋</h1>
                    <div class="date">${dateStr} · ${timeStr}</div>
                </div>
            </div>
            <div class="section-label">🎯 ที่ต้องทำตอนนี้</div>
            <div class="priority-list">${priorityHtml}</div>
            <div class="section-label">📊 วันนี้</div>
            <div class="mini-stats">
                <div class="mini-stat"><div class="ms-label">รายได้วันนี้</div><div class="ms-val">฿${Number(todayRev).toLocaleString()}</div><div style="font-size:0.75rem;color:${todayChange>=0?"var(--success)":"var(--error)"};font-weight:600;margin-top:0.2rem;">${todayChange>=0?"↑":"↓"} ${Math.abs(todayChange)}% vs เมื่อวาน</div></div>
                <div class="mini-stat"><div class="ms-label">รายได้สัปดาห์นี้</div><div class="ms-val">฿${Number(weekRev).toLocaleString()}</div></div>
                <div class="mini-stat"><div class="ms-label">รายได้เดือนนี้</div><div class="ms-val">฿${Number(monthRev).toLocaleString()}</div></div>
                <div class="mini-stat"><div class="ms-label">หมดอายุวันนี้</div><div class="ms-val">${expToday}</div></div>
            </div>
            <div class="section-label">⚡ Quick Actions</div>
            <div class="qa-grid">
                <button class="qa-btn" onclick="openGroupBroadcastModal()">📣 บอกข่าวลงกลุ่ม</button>
                <button class="qa-btn" onclick="navigate('promotions')">🎁 จัดโปร</button>
                <button class="qa-btn" onclick="settingsTab='botmsg';navigate('settings');">💬 แก้คำพูดบอท</button>
                <button class="qa-btn" onclick="navigate('customers')">🔍 หาลูกค้า</button>
                <button class="qa-btn" onclick="openDailyReportModal()">📊 รายงานวันนี้</button>
                <button class="qa-btn" onclick="openExportsModal()">📥 Export Excel</button>
                <button class="qa-btn" onclick="navigate('receivers')">💳 บัญชีรับเงิน</button>
                <button class="qa-btn" onclick="navigate('gacha')">🎰 กาชา</button>
            </div>
            <div style="margin-top:1.5rem;padding:1rem 1.25rem;background:var(--surface-2);border-radius:var(--radius);font-size:0.8rem;color:var(--text-muted);">
                💡 หน้านี้คือ "งานวันนี้" — เปิดเช้ามาดูที่นี่ก่อน · ที่ <span class="tag" style="background:rgba(247,176,69,0.1);color:var(--primary);padding:0.1rem 0.5rem;border-radius:6px;font-weight:600;">🚦 ฟีเจอร์ใหม่</span> ในแท็บ <a href="javascript:navigate(\'settings\')" style="color:var(--accent);">ตั้งค่า</a> เปิด/ปิด feature ใหม่ได้
            </div>
        `;
    } catch (e) {
        content.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><p>โหลดไม่สำเร็จ: ${esc(e.message)}</p></div>`;
    }
}

// ----- Feature Flags tab -----
async function loadFeatureFlags() {
    const area = document.getElementById('settings-area');
    try {
        const flags = await api('/feature-flags');
        let html = `<div style="background:rgba(247,176,69,0.08);border:1px solid rgba(247,176,69,0.3);border-radius:var(--radius-lg);padding:0.9rem 1.1rem;margin-bottom:1rem;">
            <div style="font-weight:600;color:var(--warning);margin-bottom:0.2rem;">🚦 Feature Flags = สวิตช์เปิด/ปิด</div>
            <div style="font-size:0.85rem;color:var(--text-muted);">ใหม่ทุก feature จะมี flag ที่นี่ · default = OFF (ใช้พฤติกรรมเดิม) · เปิด ON สำหรับลูกค้าทุกคนได้เฉพาะ Owner</div>
        </div>`;
        html += '<div class="table-wrap"><table><thead><tr><th>Feature</th><th>คำอธิบาย</th><th>Scope</th><th>สถานะ</th><th>Action</th></tr></thead><tbody>';
        flags.forEach(f => {
            const statusColor = f.enabled ? 'var(--success)' : 'var(--text-dim)';
            const statusText = f.enabled ? '✅ ON' : '⏸ OFF';
            html += `<tr>
                <td><code style="font-family:var(--font-mono);font-size:0.8rem;">${esc(f.flag_key)}</code></td>
                <td style="font-size:0.85rem;color:var(--text-muted);">${esc(f.description || '')}</td>
                <td><span class="tag" style="background:var(--surface-2);padding:0.15rem 0.5rem;border-radius:6px;font-size:0.75rem;">${esc(f.scope)}</span></td>
                <td style="color:${statusColor};font-weight:600;">${statusText}</td>
                <td>
                    <button class="btn btn-sm btn-outline" onclick="toggleFlag('${esc(f.flag_key)}', ${!f.enabled})">${f.enabled ? '⏸ ปิด' : '▶ เปิด'}</button>
                    <button class="btn btn-sm btn-outline" onclick="editFlagScope('${esc(f.flag_key)}', '${esc(f.scope)}', ${JSON.stringify(f.canary_user_ids || []).replace(/"/g,'&quot;')})">⚙️ Scope</button>
                </td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        area.innerHTML = html;
    } catch (e) {
        area.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><p>${esc(e.message)}</p></div>`;
    }
}

async function toggleFlag(flagKey, newEnabled) {
    // Phase A.2 (2026-06-27): strong confirm for enabling at scope=all
    try {
        if (newEnabled) {
            const flag = await api(`/feature-flags/${flagKey}`).catch(() => null);
            const scope = flag?.scope || 'all';
            const scopeText = scope === 'all' ? 'ลูกค้าทุกคน 🚨' : (scope === 'canary' ? 'canary list' : 'admin เท่านั้น');
            const msg = `✅ จะเปิด feature นี้ใช่ไหม?\n\n\n` +
                `Feature : ${flagKey}\n` +
                `Scope   : ${scope} (${scopeText})\n\n` +
                (scope === 'all' ? `⚠️ ระวัง: จะเปิดให้ลูกค้าทุกคนทันที! ถ้ายังไม่ทดสอบ เปลี่ยน scope = canary ก่อน` : '✓ ทดสอบกับ canary list ก่อน — ปลอดภัย');
            if (!await confirmModal({ message: msg, dangerous: true })) return;
        }
        await api(`/feature-flags/${flagKey}`, { method: 'PATCH', body: JSON.stringify({ enabled: newEnabled }) });
        toast(`✅ ${flagKey} → ${newEnabled ? 'ON' : 'OFF'}`, 'success');
        loadFeatureFlags();
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

function editFlagScope(flagKey, currentScope, canaryIds) {
    openModal('⚙️ Scope: ' + flagKey, `
        <div class="form-group">
            <label>ใครได้ feature นี้?</label>
            <select id="flag-scope">
                <option value="all" ${currentScope==='all'?'selected':''}>ทุกคน (ลูกค้าทั้งหมด)</option>
                <option value="admin" ${currentScope==='admin'?'selected':''}>เฉพาะ admin เท่านั้น</option>
                <option value="canary" ${currentScope==='canary'?'selected':''}>เฉพาะ user ที่ระบุ (canary test)</option>
            </select>
        </div>
        <div class="form-group">
            <label>Canary Telegram IDs (คั่นด้วย comma)</label>
            <input id="flag-canary" placeholder="เช่น 8502597269,1234567890" value="${(canaryIds||[]).join(',')}">
            <small style="color:var(--text-dim);">ใช้เฉพาะเมื่อ scope = canary</small>
        </div>
        <button class="btn btn-primary btn-full" onclick="saveFlagScope('${flagKey}')">💾 บันทึก</button>
    `);
}

async function saveFlagScope(flagKey) {
    const scope = document.getElementById('flag-scope').value;
    const canaryStr = document.getElementById('flag-canary').value || '';
    const canary_user_ids = canaryStr.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
    try {
        await api(`/feature-flags/${flagKey}`, { method: 'PATCH', body: JSON.stringify({ scope, canary_user_ids }) });
        toast('✅ บันทึก scope แล้ว', 'success');
        closeModal();
        loadFeatureFlags();
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

// ----- Bot Messages tab -----
async function loadBotMessages() {
    const area = document.getElementById('settings-area');
    try {
        const msgs = await api('/bot-messages');
        let html = `<div style="background:rgba(0,112,243,0.06);border:1px solid rgba(0,112,243,0.2);border-radius:var(--radius-lg);padding:0.9rem 1.1rem;margin-bottom:1rem;">
            <div style="font-weight:600;color:var(--accent);margin-bottom:0.2rem;">💬 คำพูดบอท</div>
            <div style="font-size:0.85rem;color:var(--text-muted);">แก้ข้อความที่ลูกค้าเห็น · มี version history + undo · ต้องเปิด flag <code>bot_messages_enabled</code> ที่ tab "🚦 ฟีเจอร์ใหม่" ก่อนถึงจะใช้งาน</div>
        </div>
        <button class="btn btn-primary" onclick="showCreateMessage()" style="margin-bottom:1rem;">+ เพิ่มคำพูดใหม่</button>`;

        if (!msgs.length) {
            html += `<div class="empty-state" style="padding:3rem 1rem;"><div class="icon">💬</div><p>ยังไม่มีคำพูดบอทในระบบ — ทุกอย่างใช้ค่า hardcoded เดิม<br>เพิ่มคำพูดใหม่เพื่อเริ่มใช้งาน DB-managed</p></div>`;
        } else {
            const byCat = {};
            msgs.forEach(m => { (byCat[m.category] = byCat[m.category] || []).push(m); });
            Object.entries(byCat).forEach(([cat, items]) => {
                html += `<h3 style="font-size:0.85rem;margin:1rem 0 0.5rem;color:var(--text-muted);">📁 ${esc(cat)}</h3>`;
                html += '<div class="table-wrap"><table><thead><tr><th>Key</th><th>คำอธิบาย</th><th>ข้อความ (preview)</th><th>แก้ล่าสุด</th><th></th></tr></thead><tbody>';
                items.forEach(m => {
                    const preview = (m.content_html || '').replace(/<[^>]*>/g,'').substring(0, 80);
                    html += `<tr>
                        <td><code style="font-family:var(--font-mono);font-size:0.75rem;">${esc(m.message_key)}</code></td>
                        <td style="font-size:0.8rem;">${esc(m.description || '')}</td>
                        <td style="font-size:0.8rem;color:var(--text-muted);">${esc(preview)}${preview.length >= 80 ? '...' : ''}</td>
                        <td style="font-size:0.75rem;color:var(--text-dim);">${m.updated_at ? fmtDateTime(m.updated_at) : '-'}</td>
                        <td>
                            <button class="btn btn-sm btn-outline" onclick="editBotMessage('${esc(m.message_key)}')">✏️ แก้</button>
                            ${admin && admin.role === 'owner' ? `<button class="btn btn-sm btn-outline" onclick="deleteBotMessage('${esc(m.message_key)}')">🗑</button>` : ''}
                        </td>
                    </tr>`;
                });
                html += '</tbody></table></div>';
            });
        }
        area.innerHTML = html;
    } catch (e) {
        area.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><p>${esc(e.message)}</p></div>`;
    }
}

function showCreateMessage() {
    openModal('+ เพิ่มคำพูดใหม่', `
        <div class="form-group"><label>Key (เช่น welcome_new)</label><input id="bm-key" placeholder="welcome_new"></div>
        <div class="form-group"><label>คำอธิบาย (ให้ลูกน้องเข้าใจ)</label><input id="bm-desc" placeholder="ข้อความ /start ลูกค้าใหม่"></div>
        <div class="form-group"><label>Category</label>
            <select id="bm-cat">
                <option value="start">start (เริ่มแชท)</option>
                <option value="packages">packages (แพ็กเกจ)</option>
                <option value="payment">payment (จ่ายเงิน)</option>
                <option value="welcome">welcome (ต้อนรับ VIP)</option>
                <option value="renewal">renewal (เตือนต่ออายุ)</option>
                <option value="expired">expired (หมดอายุ)</option>
                <option value="general">general (อื่นๆ)</option>
            </select>
        </div>
        <div class="form-group"><label>เนื้อหา HTML</label><textarea id="bm-content" style="min-height:140px;font-family:var(--font-mono);font-size:0.85rem;" placeholder="<b>สวัสดีค่า~</b>&#10;ยินดีต้อนรับสู่ VIP เจริญพร"></textarea></div>
        <button class="btn btn-primary btn-full" onclick="createBotMessage()">💾 บันทึก</button>
    `);
}

async function createBotMessage() {
    const body = {
        message_key: document.getElementById('bm-key').value.trim(),
        description: document.getElementById('bm-desc').value.trim(),
        category: document.getElementById('bm-cat').value,
        content_html: document.getElementById('bm-content').value,
    };
    if (!body.message_key || !body.content_html) { toast('กรอกครบทุกช่อง', 'error'); return; }
    try {
        await api('/bot-messages', { method: 'POST', body: JSON.stringify(body) });
        toast('✅ เพิ่มแล้ว', 'success');
        closeModal();
        loadBotMessages();
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

async function editBotMessage(key) {
    try {
        const m = await api(`/bot-messages/${encodeURIComponent(key)}`);
        const versionsHtml = (m.versions || []).slice(0, 5).map(v => `
            <div style="padding:0.5rem;background:var(--surface-2);border-radius:6px;margin-bottom:0.3rem;font-size:0.75rem;">
                <div style="color:var(--text-dim);">${fmtDateTime(v.changed_at)} · ${esc(v.change_note || '')}</div>
                <button class="btn btn-sm btn-outline" onclick="restoreVersion('${esc(key)}', ${v.id})" style="margin-top:0.3rem;">⏮ ย้อนกลับ version นี้</button>
            </div>
        `).join('');
        // Phase A.2 fix: live preview integrated into editBotMessage modal
        openModal('✏️ แก้คำพูด: ' + key, `
            <div class="form-group"><label>คำอธิบาย</label><input id="bm-desc-edit" value="${esc(m.description || '')}"></div>
            <div class="form-group"><label>เนื้อหา HTML</label>
                <textarea id="bm-content-edit" oninput="document.getElementById('bm-preview-${esc(key)}').innerHTML = (this.value || '').replace(/<script[\\s\\S]*?<\\/script>/gi, '').replace(/<iframe[\\s\\S]*?<\\/iframe>/gi, '')" style="min-height:160px;font-family:var(--font-mono);font-size:0.85rem;">${esc(m.content_html || '')}</textarea>
            </div>
            <div class="form-group">
                <label>👀 Preview ใน Telegram</label>
                <div style="background:#cad3df;padding:0.85rem;border-radius:8px;">
                    <div style="background:white;padding:0.65rem 0.85rem;border-radius:14px 14px 14px 4px;max-width:90%;font-size:0.875rem;line-height:1.55;box-shadow:0 1px 2px rgba(0,0,0,0.08);">
                        <div id="bm-preview-${esc(key)}">${m.content_html || '<i style=color:#999;>พิมพ์ข้อความใน textarea ด้านบน</i>'}</div>
                    </div>
                </div>
            </div>
            <div class="form-group"><label>หมายเหตุการแก้ (optional)</label><input id="bm-note-edit" placeholder="เช่น เพิ่ม emoji หัวข้อ"></div>
            <button class="btn btn-primary btn-full" onclick="saveBotMessage('${esc(key)}')">💾 บันทึก + เก็บ version</button>
            ${versionsHtml ? `<div style="margin-top:1rem;"><h4 style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.5rem;">📜 Version history (5 ล่าสุด)</h4>${versionsHtml}</div>` : ''}
        `);
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

async function saveBotMessage(key) {
    const body = {
        content_html: document.getElementById('bm-content-edit').value,
        description: document.getElementById('bm-desc-edit').value.trim(),
        change_note: document.getElementById('bm-note-edit').value.trim() || 'manual edit',
    };
    try {
        await api(`/bot-messages/${encodeURIComponent(key)}`, { method: 'PATCH', body: JSON.stringify(body) });
        toast('✅ บันทึกแล้ว', 'success');
        closeModal();
        loadBotMessages();
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

async function restoreVersion(key, versionId) {
    if (!await confirmModal({ message: 'ย้อนกลับไป version นี้?', dangerous: true })) return;
    try {
        await api(`/bot-messages/${encodeURIComponent(key)}/restore/${versionId}`, { method: 'POST' });
        toast('✅ ย้อนกลับแล้ว', 'success');
        closeModal();
        loadBotMessages();
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

async function deleteBotMessage(key) {
    if (!await confirmModal({ message: `ลบคำพูด "${key}"? ระบบจะ fallback ไปใช้ค่า hardcoded`, dangerous: true })) return;
    try {
        await api(`/bot-messages/${encodeURIComponent(key)}`, { method: 'DELETE' });
        toast('✅ ลบแล้ว', 'success');
        loadBotMessages();
    } catch (e) { toast('❌ ' + e.message, 'error'); }
}

// ==================================================================
// Phase B.1 (2026-06-27): Promo Manager
// Replaces renderPromotions in dispatcher. Old renderPromotions kept
// as "📜 Campaign เก่า" tab for legacy access.
// ==================================================================
// promoTab declared at line 2565 (reused)

async function renderPromoManager() {
    const content = document.getElementById('page-content');
    content.innerHTML = `
        <div class="tabs">
            <div class="tab ${promoTab==='campaigns_new'?'active':''}" onclick="promoTab='campaigns_new';renderPromoManager()">🎁 จัดการโปร</div>
            <div class="tab ${promoTab==='comeback'?'active':''}" onclick="promoTab='comeback';renderPromoManager()">📩 Comeback DM</div>
            <div class="tab ${promoTab==='quickbuy'?'active':''}" onclick="promoTab='quickbuy';renderPromoManager()">⚡ ซื้อเร็ว /start</div>
            <div class="tab ${promoTab==='gacha_discount'?'active':''}" onclick="promoTab='gacha_discount';renderPromoManager()">💰 ส่วนลดกาชา</div>
            <div class="tab ${promoTab==='welcome_journey'?'active':''}" onclick="promoTab='welcome_journey';renderPromoManager()">👋 Welcome 24h</div>
            <div class="tab ${promoTab==='retention'?'active':''}" onclick="promoTab='retention';renderPromoManager()">⏰ เตือนต่ออายุ</div>
            <div class="tab ${promoTab==='exit_survey'?'active':''}" onclick="promoTab='exit_survey';renderPromoManager()">🚪 Exit Survey</div>
            <div class="tab ${promoTab==='group_bot'?'active':''}" onclick="promoTab='group_bot';renderPromoManager()">🏛 บอทในกลุ่ม</div>
            <div class="tab ${promoTab==='old_campaigns'?'active':''}" onclick="promoTab='old_campaigns';renderPromoManager()">📜 เก่า</div>
        </div>
        <div id="promo-area"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (promoTab === 'campaigns_new') loadDayZeroPromos();
    else if (promoTab === 'comeback') loadComebackConfig();
    else if (promoTab === 'quickbuy') loadQuickBuyConfig();
    else if (promoTab === 'gacha_discount') loadGachaDiscountConfig();
    else if (promoTab === 'welcome_journey') loadWelcomeConfig();
    else if (promoTab === 'retention') loadRetentionConfig();
    else if (promoTab === 'exit_survey') loadExitSurveyConfig();
    else if (promoTab === 'group_bot') loadGroupBotConfig();
    else if (promoTab === 'old_campaigns') {
        document.getElementById('promo-area').innerHTML = '<div style="padding:1rem;">โหลด UI campaign เก่า...</div>';
        try {
            if (typeof renderPromotions === 'function') {
                // Switch to old promotions UI
                await renderPromotions();
            } else {
                document.getElementById('promo-area').innerHTML = '<div class="empty-state"><div class="icon">📜</div><p>ระบบเก่า — เก็บไว้ดูเฉยๆ ไม่ได้ใช้แล้ว</p></div>';
            }
        } catch(e) {
            document.getElementById('promo-area').innerHTML = '<div class="empty-state"><div class="icon">⚠️</div><p>' + e.message + '</p></div>';
        }
    }
}

function _renderConfigRow(c) {
    const isBool = typeof c.value_json === 'boolean';
    const isNumber = typeof c.value_json === 'number';
    const isDict = typeof c.value_json === 'object' && c.value_json !== null && !Array.isArray(c.value_json);
    let inputHtml = '';
    if (isBool) {
        inputHtml = `<label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;">
            <input type="checkbox" id="cfg-${esc(c.config_key)}" ${c.value_json?'checked':''}>
            <span>${c.value_json?'เปิด':'ปิด'}</span>
        </label>`;
    } else if (isNumber) {
        inputHtml = `<input type="number" id="cfg-${esc(c.config_key)}" value="${c.value_json}" style="width:120px;padding:0.4rem 0.6rem;border:1px solid var(--border);border-radius:6px;">`;
    } else if (isDict) {
        inputHtml = `<textarea id="cfg-${esc(c.config_key)}" style="width:100%;min-height:80px;padding:0.5rem;font-family:var(--font-mono);font-size:0.8rem;border:1px solid var(--border);border-radius:6px;">${esc(JSON.stringify(c.value_json, null, 2))}</textarea>`;
    } else {
        inputHtml = `<input type="text" id="cfg-${esc(c.config_key)}" value="${esc(c.value_json)}" style="width:240px;padding:0.4rem 0.6rem;border:1px solid var(--border);border-radius:6px;">`;
    }
    return `<tr>
        <td style="vertical-align:top;">
            <code style="font-family:var(--font-mono);font-size:0.75rem;">${esc(c.config_key)}</code>
            <div style="color:var(--text-dim);font-size:0.75rem;margin-top:0.2rem;">${esc(c.description||'')}</div>
        </td>
        <td style="vertical-align:top;">${inputHtml}</td>
        <td style="vertical-align:top;">
            <button class="btn btn-sm btn-primary" onclick="savePromoConfig('${esc(c.config_key)}', '${isBool?'bool':isNumber?'number':isDict?'dict':'string'}')">💾 บันทึก</button>
        </td>
    </tr>`;
}

async function _loadConfigCategory(category, titleHtml, helpHtml) {
    const area = document.getElementById('promo-area');
    try {
        const items = await api('/promo-manager?category=' + category);
        let html = titleHtml;
        if (helpHtml) html += helpHtml;
        if (!items.length) {
            html += '<div class="empty-state"><div class="icon">⚙️</div><p>ยังไม่มี config ในหมวด ' + category + '</p></div>';
        } else {
            html += '<div class="table-wrap"><table><thead><tr><th>Setting</th><th>ค่าปัจจุบัน</th><th></th></tr></thead><tbody>';
            items.forEach(c => { html += _renderConfigRow(c); });
            html += '</tbody></table></div>';
        }
        area.innerHTML = html;
    } catch (e) {
        area.innerHTML = '<div class="empty-state"><div class="icon">⚠️</div><p>' + esc(e.message) + '</p></div>';
    }
}

async function savePromoConfig(key, dtype) {
    const el = document.getElementById('cfg-' + key);
    let value;
    if (dtype === 'bool') value = el.checked;
    else if (dtype === 'number') value = parseFloat(el.value);
    else if (dtype === 'dict') {
        try { value = JSON.parse(el.value); } catch (e) { toast('❌ JSON ผิด format', 'error'); return; }
    } else value = el.value;
    try {
        await api('/promo-manager/' + key, { method: 'PATCH', body: JSON.stringify({ value_json: value }) });
        toast('✅ บันทึก ' + key, 'success');
        await api('/promo-manager/cache-clear', { method: 'POST' }).catch(()=>{});
    } catch (e) {
        toast('❌ ' + e.message, 'error');
    }
}

async function loadComebackConfig() {
    const helpHtml = `
    <div style="background:rgba(0,112,243,0.06);border:1px solid rgba(0,112,243,0.2);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:var(--accent);margin-bottom:0.3rem;">📩 Comeback DM</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            ระบบส่ง DM ลูกค้าที่หมดอายุ → ลด% ดึงกลับมา<br>
            <b>รอบ 1:</b> หลังหมดอายุ X วัน → ส่งลด Y%<br>
            <b>รอบ 2:</b> หลังรอบ 1 ผ่าน X วัน + ลูกค้ายังไม่ซื้อ → ส่งลด Y%<br>
            <br>⚠️ ต้องเปิด flag <code>comeback_config_from_db</code> ในแท็บ ฟีเจอร์ใหม่ ก่อนถึงจะใช้งาน
        </div>
    </div>`;
    await _loadConfigCategory('comeback', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่า Comeback DM</h3>', helpHtml);
}

async function loadQuickBuyConfig() {
    const helpHtml = `
    <div style="background:rgba(247,176,69,0.08);border:1px solid rgba(247,176,69,0.3);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:var(--warning);margin-bottom:0.3rem;">⚡ ซื้อเร็วตอน /start</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            เมื่อลูกค้าเก่ากลับมา (ผ่าน Comeback DM) แล้วกด /start จากลิงก์ → แสดงปุ่มซื้อทันที + ส่วนลด<br>
            <b>ใช้ %ลด:</b> ที่กำหนดไว้ใน promo code ของ Comeback (หรือใช้ default ถ้าไม่ระบุ)
        </div>
    </div>`;
    await _loadConfigCategory('quickbuy', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่า Quick Buy</h3>', helpHtml);
}

async function loadGachaDiscountConfig() {
    const helpHtml = `
    <div style="background:rgba(244,114,182,0.08);border:1px solid rgba(244,114,182,0.3);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:#9D174D;margin-bottom:0.3rem;">💰 ส่วนลดจากกาชา</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            ลูกค้าหมุนกาชา → ลุ้นได้ส่วนลด ฿50/หมุน เก็บไว้ใช้ตอนซื้อแพ็กเกจ<br>
            <b>เพดาน:</b> แต่ละแพ็กเกจใช้ส่วนลดได้สูงสุด (JSON)<br>
            <br>⚠️ <b>การจัดการรางวัลรายตัว</b> อยู่ที่ 🎰 กาชา · ที่นี่เป็นกฎรวม
        </div>
    </div>`;
    await _loadConfigCategory('gacha_discount', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่ารางวัลกาชา</h3>', helpHtml);
}

async function loadGroupBotConfig() {
    const helpHtml = `
    <div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:#065F46;margin-bottom:0.3rem;">🏛 บอทในกลุ่ม Telegram</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            พฤติกรรมของ Guardian + Content bot ในกลุ่ม VIP/ฟรี<br>
            <b>Welcome:</b> ทักทายสมาชิกใหม่ที่เข้ากลุ่ม<br>
            <b>Daily Content:</b> โพสต์รูปประจำวันตามตาราง
        </div>
    </div>`;
    await _loadConfigCategory('group_bot', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่าบอทในกลุ่ม</h3>', helpHtml);
}

async function loadWelcomeConfig() {
    const helpHtml = `
    <div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:#065F46;margin-bottom:0.3rem;">👋 Welcome Journey V2 (24 ชม.)</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            ลูกค้าใหม่กด /start → ส่ง DM 4 ครั้ง (instant + 3h + 12h + 23h) ลด 25%<br>
            <br>⚠️ ต้องเปิด flag <code>welcome_config_from_db</code> ในแท็บ ฟีเจอร์ใหม่ ก่อน
        </div>
    </div>`;
    await _loadConfigCategory('welcome_journey', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่า Welcome Journey</h3>', helpHtml);
}

async function loadRetentionConfig() {
    const helpHtml = `
    <div style="background:rgba(217,119,6,0.08);border:1px solid rgba(217,119,6,0.3);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:#92400E;margin-bottom:0.3rem;">⏰ Retention Alert (ก่อนหมดอายุ)</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            ส่ง DM ลูกค้า ACTIVE ก่อนหมดอายุ<br>
            <b>3 วันก่อนหมด:</b> ลด 10% &nbsp; <b>1 วัน:</b> ลด 15% &nbsp; <b>วันหมด:</b> ลด 20%<br>
            <br>⚠️ ต้องเปิด flag <code>retention_config_from_db</code> ก่อน
        </div>
    </div>`;
    await _loadConfigCategory('retention', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่า Retention Alert</h3>', helpHtml);
}

async function loadExitSurveyConfig() {
    const helpHtml = `
    <div style="background:rgba(220,38,38,0.05);border:1px solid rgba(220,38,38,0.3);border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem;font-size:0.85rem;">
        <div style="font-weight:600;color:var(--error);margin-bottom:0.3rem;">🚪 Exit Survey (หลังหมด 24-48 ชม.)</div>
        <div style="color:var(--text-muted);line-height:1.7;">
            ส่ง DM ลูกค้าที่หมดอายุแล้ว 24-48 ชม. ขอเหตุผล + เสนอส่วนลด<br>
            <b>VIP 300:</b> ลด 50% &nbsp; <b>OF+VIP 500:</b> 40% &nbsp; <b>GOD 1299:</b> 30% &nbsp; <b>ถาวร 2499:</b> 20%<br>
            <br>⚠️ ต้องเปิด flag <code>exit_survey_config_from_db</code> ก่อน
        </div>
    </div>`;
    await _loadConfigCategory('exit_survey', '<h3 style="margin-bottom:1rem;">⚙️ ตั้งค่า Exit Survey</h3>', helpHtml);
}

// ==================================================================
// Phase A.2 (2026-06-27): Customer Notes + Rejection Reasons preset
// ==================================================================

async function showRejectReasonModal(paymentId) {
    let reasons = [];
    try { reasons = await api('/rejection-reasons'); } catch(e) { reasons = []; }
    const opts = reasons.map(r => `
        <label style="display:block;padding:0.65rem 0.85rem;border:1px solid var(--border);border-radius:8px;margin-bottom:0.4rem;cursor:pointer;font-size:0.9rem;">
            <input type="radio" name="rej-reason" value="${r.id}" data-msg="${esc((r.customer_message||'').replace(/"/g,'&quot;'))}" data-label="${esc(r.label)}"
                   onchange="document.getElementById('rej-custom').value = this.dataset.msg || ''">
            <b style="margin-left:0.4rem;">${esc(r.label)}</b>
            ${r.customer_message ? `<div style="color:var(--text-muted);font-size:0.78rem;margin-top:0.2rem;margin-left:1.4rem;">${esc(r.customer_message.substring(0, 80))}${r.customer_message.length > 80 ? '...' : ''}</div>` : ''}
        </label>
    `).join('');
    openModal('❌ Reject สลิป #' + paymentId, `
        <div style="margin-bottom:0.8rem;font-size:0.85rem;color:var(--text-muted);">เลือกเหตุผล (ลูกค้าจะได้รับข้อความ DM):</div>
        ${opts}
        <div class="form-group" style="margin-top:1rem;">
            <label>ข้อความที่ลูกค้าจะได้รับ (แก้ได้)</label>
            <textarea id="rej-custom" style="width:100%;min-height:80px;padding:0.55rem;font-family:inherit;font-size:0.85rem;border:1px solid var(--border);border-radius:6px;" placeholder="เลือกเหตุผลด้านบน หรือพิมพ์เอง..."></textarea>
        </div>
        <button class="btn btn-danger btn-full" onclick="confirmRejectPayment(${paymentId})">❌ Reject + ส่ง DM</button>
    `);
}

async function confirmRejectPayment(paymentId) {
    const radio = document.querySelector('input[name="rej-reason"]:checked');
    const customMsg = document.getElementById('rej-custom').value.trim();
    if (!radio && !customMsg) { toast('เลือกเหตุผลหรือพิมพ์ข้อความ', 'error'); return; }
    const label = radio ? radio.dataset.label : 'อื่นๆ';
    const message = customMsg || (radio ? radio.dataset.msg : '');
    try {
        await api('/payments/' + paymentId + '/reject', {
            method: 'POST',
            body: JSON.stringify({ reason: label, customer_message: message })
        });
        toast('❌ Reject + DM ส่งแล้ว', 'success');
        closeModal();
        renderInbox();
    } catch (err) {
        toast('❌ ' + (err.message || 'ทำไม่สำเร็จ'), 'error');
    }
}

async function renderCustomerNotes(userId, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    try {
        const notes = await api('/customers/' + userId + '/notes');
        let html = `<div style="margin-bottom:0.6rem;">
            <textarea id="note-new-${userId}" style="width:100%;padding:0.55rem;border:1px solid var(--border);border-radius:6px;font-family:inherit;font-size:0.85rem;min-height:60px;" placeholder="เขียนโน้ตให้ทีม (เช่น: ลูกค้าพิเศษ ทักตอนเย็น)..."></textarea>
            <div style="margin-top:0.4rem;display:flex;gap:0.4rem;">
                <label style="display:flex;align-items:center;gap:0.3rem;font-size:0.8rem;cursor:pointer;">
                    <input type="checkbox" id="note-pin-${userId}"> 📌 ปักหมุด
                </label>
                <button class="btn btn-primary btn-sm" style="margin-left:auto;" onclick="saveCustomerNote(${userId})">💾 บันทึก</button>
            </div>
        </div>`;
        if (!notes.length) {
            html += '<div style="text-align:center;color:var(--text-dim);font-size:0.85rem;padding:1rem;">ยังไม่มีโน้ต</div>';
        } else {
            notes.forEach(n => {
                html += `<div style="padding:0.7rem 0.85rem;background:${n.is_pinned ? 'rgba(247,176,69,0.08)' : 'var(--surface-2)'};border-radius:6px;margin-bottom:0.5rem;border-left:${n.is_pinned ? '3px solid var(--primary)' : 'none'};">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.25rem;">
                        <span style="font-size:0.72rem;color:var(--text-dim);">
                            ${n.is_pinned ? '📌 ' : ''}${fmtDateTime(n.created_at)} · by ${n.created_by || 'admin'}
                        </span>
                        <div style="display:flex;gap:0.3rem;">
                            <button class="btn btn-sm btn-outline" onclick="toggleNotePin(${userId}, ${n.id}, ${!n.is_pinned})" title="${n.is_pinned ? 'ถอนหมุด' : 'ปักหมุด'}">${n.is_pinned ? '📍' : '📌'}</button>
                            <button class="btn btn-sm btn-outline" onclick="deleteCustomerNote(${userId}, ${n.id})" title="ลบ">🗑</button>
                        </div>
                    </div>
                    <div style="font-size:0.88rem;white-space:pre-wrap;">${esc(n.content)}</div>
                </div>`;
            });
        }
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div style="color:var(--error);font-size:0.85rem;">โหลดโน้ตไม่สำเร็จ: ' + esc(e.message) + '</div>';
    }
}

async function saveCustomerNote(userId) {
    const inputEl = document.getElementById('note-new-' + userId);
    const pinEl = document.getElementById('note-pin-' + userId);
    const content = inputEl?.value.trim();
    if (!content) { toast('พิมพ์โน้ตก่อน', 'error'); return; }
    const isPinned = pinEl?.checked;
    try {
        await api('/customers/' + userId + '/notes', {
            method: 'POST',
            body: JSON.stringify({ content: content, is_pinned: isPinned })
        });
        toast('✅ บันทึกโน้ตแล้ว', 'success');
        // Phase A.2 fix: clear input + force re-render with await (was racing before)
        if (inputEl) inputEl.value = '';
        if (pinEl) pinEl.checked = false;
        await renderCustomerNotes(userId, 'notes-' + userId);
    } catch (err) {
        toast('❌ ' + err.message, 'error');
    }
}

async function toggleNotePin(userId, noteId, newPinState) {
    try {
        await api('/customers/' + userId + '/notes/' + noteId, {
            method: 'PATCH',
            body: JSON.stringify({ is_pinned: newPinState })
        });
        renderCustomerNotes(userId, 'notes-' + userId);
    } catch (err) { toast('❌ ' + err.message, 'error'); }
}

async function deleteCustomerNote(userId, noteId) {
    if (!await confirmModal({ message: 'ลบโน้ตนี้?', dangerous: true })) return;
    try {
        await api('/customers/' + userId + '/notes/' + noteId, { method: 'DELETE' });
        toast('✅ ลบแล้ว', 'success');
        renderCustomerNotes(userId, 'notes-' + userId);
    } catch (err) { toast('❌ ' + err.message, 'error'); }
}

// ==================================================================
// Phase A.2 (2026-06-27): Test mode banner — detects X-Dashboard-Test-Mode header
// ==================================================================
async function checkTestMode() {
    try {
        const resp = await fetch("/api/dashboard/alerts", {
            headers: token ? { "Authorization": "Bearer " + token } : {}
        });
        if (resp.headers.get("X-Dashboard-Test-Mode") === "true" && !document.getElementById("test-mode-banner")) {
            // Update page title
            document.title = "🟡 TEST · " + document.title;
            // Inject CSS that shifts sidebar AND main down so banner fits
            const css = document.createElement("style");
            css.id = "test-mode-css";
            css.textContent = `
                body { padding-top: 36px !important; }
                .sidebar { top: 36px !important; }
                .top-bar { top: 36px !important; }
                #test-mode-banner { position: fixed; top: 0; left: 0; right: 0; z-index: 10000;
                    background: linear-gradient(135deg, #DC2626 0%, #991B1B 100%);
                    color: white; text-align: center; padding: 0.4rem 1rem;
                    font-weight: 700; font-size: 0.8rem; letter-spacing: 0.04em;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3); height: 36px;
                    display: flex; align-items: center; justify-content: center; gap: 0.5rem; }
            `;
            document.head.appendChild(css);
            // Add banner
            const banner = document.createElement("div");
            banner.id = "test-mode-banner";
            banner.innerHTML = "🟡 <b>TEST MODE</b> — กดอะไรก็ได้ ไม่กระทบลูกค้าจริง · port 8012";
            document.body.insertBefore(banner, document.body.firstChild);
        }
    } catch (_e) {}
}

// ==================================================================
// Phase A.3 (2026-06-27): Marketing heatmap (7 days × 24 hours)
// ==================================================================
async function renderMarketingHeatmap(containerId, days = 30) {
    const el = document.getElementById(containerId);
    if (!el) return;
    try {
        const data = await api('/marketing/heatmap?days=' + days);
        if (!data.total) {
            el.innerHTML = '<div class="empty-state" style="padding:1rem;">ไม่มีข้อมูล join ใน ' + days + ' วันที่ผ่านมา</div>';
            return;
        }
        const dayLabels = data.day_labels || ['อา','จ','อ','พ','พฤ','ศ','ส'];
        const grid = data.grid; // [7][24]
        const peak = data.peak || {};

        // Find max for color scale
        let max = 0;
        for (let d = 0; d < 7; d++) for (let h = 0; h < 24; h++) if (grid[d][h] > max) max = grid[d][h];

        const cellColor = (v) => {
            if (v === 0) return 'var(--surface-2)';
            const intensity = v / max;
            // Pink gradient: surface → primary
            const alpha = 0.15 + intensity * 0.85;
            return `rgba(247, 176, 69, ${alpha})`;
        };

        let html = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;flex-wrap:wrap;gap:0.5rem;">
                <h3 style="margin:0;font-size:0.95rem;font-weight:600;">🔥 Heatmap — เวลาที่ลูกค้า join (${days} วัน)</h3>
                <div style="font-size:0.75rem;color:var(--text-muted);">
                    🏆 Peak: ${dayLabels[peak.dow]} ${String(peak.hour).padStart(2,'0')}:00 — <b>${peak.count}</b> joins
                </div>
            </div>
            <div style="overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.75rem;">
                <table style="border-collapse:separate;border-spacing:2px;font-size:0.7rem;">
                    <thead><tr>
                        <th style="padding:0.2rem 0.4rem;width:35px;"></th>`;
        for (let h = 0; h < 24; h++) {
            html += `<th style="padding:0.2rem;width:22px;text-align:center;color:var(--text-dim);font-weight:500;">${String(h).padStart(2,'0')}</th>`;
        }
        html += `<th style="padding:0.2rem 0.4rem;width:35px;color:var(--text-dim);">รวม</th></tr></thead><tbody>`;
        for (let d = 0; d < 7; d++) {
            html += `<tr><td style="padding:0.2rem 0.4rem;font-weight:500;color:var(--text-dim);">${dayLabels[d]}</td>`;
            for (let h = 0; h < 24; h++) {
                const v = grid[d][h];
                const isPeak = (d === peak.dow && h === peak.hour);
                html += `<td title="${dayLabels[d]} ${String(h).padStart(2,'0')}:00 — ${v} joins" style="width:22px;height:22px;background:${cellColor(v)};text-align:center;border-radius:3px;color:${v>max*0.5?'var(--text)':'var(--text-muted)'};font-size:0.65rem;${isPeak?'box-shadow:0 0 0 2px var(--error);':''}">${v||''}</td>`;
            }
            const dayTotal = data.day_totals[d];
            html += `<td style="padding:0.2rem 0.4rem;font-weight:600;color:var(--text);">${dayTotal}</td></tr>`;
        }
        html += '</tbody></table></div>';
        html += '<div style="font-size:0.7rem;color:var(--text-dim);margin-top:0.4rem;text-align:right;">รวม ' + data.total + ' joins · max cell = ' + max + '</div>';
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<div class="empty-state" style="padding:1rem;color:var(--error);">' + e.message + '</div>';
    }
}

// ==================================================================
// Phase A.3 (2026-06-27): Relay-bot sync status widget (v2: with names)
// ==================================================================
async function renderRelayWidget(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    try {
        const r = await api('/groups/relay-status');
        if (!r.available) {
            el.innerHTML = '<div style="padding:0.75rem 1rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text-muted);font-size:0.85rem;">⚠️ Relay status unavailable: ' + (r.reason || '?') + '</div>';
            return;
        }
        const ago = (ts) => {
            if (!ts) return '-';
            const sec = Math.floor((Date.now() - ts) / 1000);
            if (sec < 60) return sec + ' วินาทีที่แล้ว';
            if (sec < 3600) return Math.floor(sec/60) + ' นาทีที่แล้ว';
            if (sec < 86400) return Math.floor(sec/3600) + ' ชม.ที่แล้ว';
            return Math.floor(sec/86400) + ' วันที่แล้ว';
        };
        const fmtTs = (iso) => {
            if (!iso) return '-';
            try {
                const d = new Date(iso);
                return d.toLocaleString('th-TH', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
            } catch (e) { return iso; }
        };
        const status = r.paused ? '⏸ PAUSED' : '✅ ทำงาน';
        const statusColor = r.paused ? 'var(--warning)' : 'var(--success)';

        const src = r.source;
        const srcHtml = src
            ? `<b>👑 ${esc(src.slug)}</b> — ${esc(src.title)} <span style="color:var(--text-dim);font-size:0.7rem;">(${src.chat_id})</span>`
            : '<span style="color:var(--text-muted);">ไม่ทราบ</span>';

        // Dest list — compact chip layout
        const dests = r.destinations || [];
        const destChips = dests.map(d => {
            const color = d.disabled ? 'var(--error)' : 'var(--success)';
            const failBadge = d.failures > 0 ? ` <span style="color:var(--warning);font-size:0.65rem;">⚠️${d.failures}</span>` : '';
            return `<span title="${esc(d.title)} (${d.chat_id})" style="display:inline-block;padding:0.15rem 0.5rem;margin:0.15rem;border:1px solid ${color};border-radius:4px;font-size:0.72rem;color:${color};">${esc(d.slug)}${failBadge}</span>`;
        }).join('');

        // Disabled groups — full row
        const disabled = r.disabled_destinations || [];
        const disabledHtml = disabled.length
            ? `<div style="margin-top:0.6rem;padding:0.6rem;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.3);border-radius:6px;">
                <div style="font-size:0.75rem;font-weight:600;color:var(--error);margin-bottom:0.3rem;">⚠️ ${disabled.length} กลุ่ม disabled (บอตเลิกส่ง):</div>
                ${disabled.map(d => `<div style="font-size:0.78rem;margin-left:0.5rem;">
                    • <b>${esc(d.slug)}</b> — ${esc(d.title)}
                    <span style="color:var(--text-dim);font-size:0.7rem;">(fail ${d.failures} ครั้ง · ${d.in_current_dests ? 'ยังอยู่ใน config' : 'ลบจาก config แล้ว — state เก่า'})</span>
                </div>`).join('')}
              </div>`
            : '';

        // Recent fails table
        const fails = r.recent_fails || [];
        const failsHtml = fails.length
            ? `<details style="margin-top:0.6rem;"><summary style="cursor:pointer;font-size:0.78rem;color:var(--text-muted);">📋 ดู ${fails.length} fail log ล่าสุด</summary>
                <div style="max-height:200px;overflow-y:auto;margin-top:0.4rem;border:1px solid var(--border);border-radius:4px;">
                <table style="width:100%;font-size:0.72rem;border-collapse:collapse;">
                    <thead style="background:rgba(0,0,0,0.2);position:sticky;top:0;">
                        <tr><th style="padding:0.3rem;text-align:left;">เวลา</th><th style="padding:0.3rem;text-align:left;">กลุ่ม</th><th style="padding:0.3rem;text-align:left;">สาเหตุ</th></tr>
                    </thead>
                    <tbody>
                        ${fails.slice().reverse().map(f => `<tr style="border-top:1px solid var(--border);">
                            <td style="padding:0.3rem;color:var(--text-dim);white-space:nowrap;">${esc(fmtTs(f.ts))}</td>
                            <td style="padding:0.3rem;"><b>${esc(f.slug || '-')}</b> ${f.title ? '— ' + esc(f.title) : ''}</td>
                            <td style="padding:0.3rem;color:var(--text-muted);">${esc(f.reason || '')}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
                </div></details>`
            : '<div style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-muted);">✅ ไม่มี fail ล่าสุดใน log</div>';

        el.innerHTML = `
            <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.85rem 1rem;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
                    <div style="font-weight:600;font-size:0.9rem;">🔄 Relay-bot sync</div>
                    <span style="color:${statusColor};font-weight:600;font-size:0.85rem;">${status}</span>
                </div>

                <div style="font-size:0.78rem;margin-bottom:0.5rem;">
                    <div style="color:var(--text-dim);font-size:0.7rem;">📤 ต้นทาง (โพสต์ที่นี่ → ส่งต่อทุกกลุ่มปลายทาง):</div>
                    <div style="padding:0.2rem 0;">${srcHtml}</div>
                </div>

                <div style="font-size:0.78rem;margin-bottom:0.5rem;">
                    <div style="color:var(--text-dim);font-size:0.7rem;margin-bottom:0.2rem;">📥 ปลายทาง ${dests.length} กลุ่ม:</div>
                    <div>${destChips}</div>
                </div>

                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0.4rem;font-size:0.78rem;padding:0.4rem 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin:0.5rem 0;">
                    <div><div style="color:var(--text-dim);font-size:0.7rem;">forward สำเร็จ</div><div style="font-weight:600;">${(r.total_forwarded || 0).toLocaleString()}</div></div>
                    <div><div style="color:var(--text-dim);font-size:0.7rem;">fail สะสม</div><div style="font-weight:600;color:${r.total_failed>0?'var(--error)':'inherit'};">${r.total_failed || 0}</div></div>
                    <div><div style="color:var(--text-dim);font-size:0.7rem;">ส่งปิดรอบ</div><div style="font-weight:600;">${(r.closings_sent || 0).toLocaleString()}</div></div>
                    <div><div style="color:var(--text-dim);font-size:0.7rem;">forward ล่าสุด</div><div style="font-weight:600;">${ago(r.last_forward_at)}</div></div>
                </div>

                ${disabledHtml}
                ${failsHtml}
            </div>`;
    } catch (e) {
        el.innerHTML = '<div style="padding:0.5rem;color:var(--error);font-size:0.85rem;">' + e.message + '</div>';
    }
}

// ==================================================================
// Phase A.4 (2026-06-27): Live log streamer (docker logs over WS)
// ==================================================================
let _logWs = null;
function openBotLogStream(container) {
    // Map common keys to actual container names
    const MAP = {
        'sales': 'charoenpon-sales-bot',
        'sales-bot': 'charoenpon-sales-bot',
        'sales_bot': 'charoenpon-sales-bot',
        'guardian': 'charoenpon-guardian-bot',
        'guardian-bot': 'charoenpon-guardian-bot',
        'admin': 'charoenpon-admin-bot',
        'admin-bot': 'charoenpon-admin-bot',
        'relay': 'charoenpon-relay-bot',
        'relay-bot': 'charoenpon-relay-bot',
        'dashboard': 'charoenpon-dashboard',
        'discord': 'charoenpon-discord-bot',
        'discord-bot': 'charoenpon-discord-bot',
    };
    const name = MAP[container] || container;

    // Close any existing
    if (_logWs) { try { _logWs.close(); } catch (e) {} _logWs = null; }

    openModal(`📋 Log — ${name}`, `
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">
            <span id="log-status" style="font-size:0.8rem;color:var(--text-dim);">เชื่อมต่อ...</span>
            <button class="btn btn-sm btn-outline" onclick="document.getElementById('log-pre').textContent='';" style="margin-left:auto;">🗑 Clear</button>
            <label style="font-size:0.8rem;display:flex;align-items:center;gap:0.3rem;cursor:pointer;">
                <input type="checkbox" id="log-autoscroll" checked> auto-scroll
            </label>
            <button class="btn btn-sm btn-outline" id="log-pause-btn" onclick="toggleLogPause()">⏸ Pause</button>
        </div>
        <pre id="log-pre" style="background:#0a0a0a;color:#0f0;padding:0.6rem;border-radius:6px;height:60vh;overflow-y:auto;font-size:0.75rem;font-family:var(--font-mono,monospace);line-height:1.3;white-space:pre-wrap;word-break:break-all;"></pre>
    `, '', 'wide');

    const _logToken = (typeof token !== 'undefined' && token) ? token : (localStorage.getItem('jwt') || '');
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/logs/${encodeURIComponent(name)}?token=${encodeURIComponent(_logToken)}`;
    const ws = new WebSocket(url);
    _logWs = ws;
    window._logPaused = false;

    ws.onopen = () => {
        const el = document.getElementById('log-status');
        if (el) el.innerHTML = '🟢 LIVE';
    };
    ws.onmessage = (ev) => {
        if (window._logPaused) return;
        try {
            const m = JSON.parse(ev.data);
            const pre = document.getElementById('log-pre');
            if (!pre) return;
            if (m.type === 'line') {
                pre.textContent += m.line + '\n';
                // Trim to last 5000 lines worth
                if (pre.textContent.length > 500000) {
                    pre.textContent = pre.textContent.slice(-300000);
                }
                if (document.getElementById('log-autoscroll')?.checked) {
                    pre.scrollTop = pre.scrollHeight;
                }
            } else if (m.type === 'eof') {
                const st = document.getElementById('log-status');
                if (st) st.innerHTML = '⚪ EOF';
            } else if (m.type === 'error') {
                const st = document.getElementById('log-status');
                if (st) st.innerHTML = '🔴 error: ' + m.error;
            }
            // ping → ignore
        } catch (e) {}
    };
    ws.onerror = () => {
        const el = document.getElementById('log-status');
        if (el) el.innerHTML = '🔴 connection error';
    };
    ws.onclose = (ev) => {
        const el = document.getElementById('log-status');
        if (el) el.innerHTML = '⚪ closed' + (ev.reason ? ': ' + ev.reason : '');
        _logWs = null;
    };
}

function toggleLogPause() {
    window._logPaused = !window._logPaused;
    const btn = document.getElementById('log-pause-btn');
    if (btn) btn.textContent = window._logPaused ? '▶ Resume' : '⏸ Pause';
}

// Close log WS when modal closes (defensive)
window.addEventListener('beforeunload', () => {
    if (_logWs) { try { _logWs.close(); } catch (e) {} _logWs = null; }
});

// ==================================================================
// Phase A.4 (2026-06-27): Bots status + live log button
// ==================================================================
async function loadBotsStatus() {
    const el = document.getElementById('promo-content');
    if (!el) return;
    el.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    const BOTS = [
        { container: 'charoenpon-sales-bot', name: 'Sales Bot (Prae)', icon: '🤖', desc: 'รับสลิป + ขายแพ็กเกจ + AI ตอบลูกค้า' },
        { container: 'charoenpon-guardian-bot', name: 'Guardian Bot', icon: '🛡', desc: 'ตรวจสมาชิกในกลุ่ม + ban' },
        { container: 'charoenpon-admin-bot', name: 'Admin Bot', icon: '👨‍💼', desc: 'แจ้งเตือนแอดมิน + คำสั่ง admin' },
        { container: 'charoenpon-relay-bot', name: 'Relay Bot', icon: '🔄', desc: 'forward โพสต์จาก VGOD ไปกลุ่มฟรี' },
        { container: 'charoenpon-discord-bot', name: 'Discord Bot', icon: '💬', desc: 'รายงานทีม + Prae Discord' },
        { container: 'charoenpon-dashboard', name: 'Dashboard', icon: '📊', desc: 'หน้าเว็บนี้' },
    ];

    // Fetch live status via simple endpoint (use existing /api/bots if available, else check via container_running)
    let statuses = {};
    try {
        // Use a simple ping: try fetching /api/dashboard/today as proxy that dashboard is up
        // For other bots, we'll show "ดู log" — the WS will fail-close if container is dead
        statuses = {}; // placeholder; UI shows "?" status, click log to see
    } catch (e) {}

    el.innerHTML = `
        <div style="background:rgba(0,212,255,0.05);border:1px solid rgba(0,212,255,0.2);border-radius:8px;padding:0.75rem 1rem;margin-bottom:1rem;font-size:0.85rem;color:var(--text-muted);">
            💡 <b>วิธีใช้:</b> คลิก "📋 ดู log สด" เพื่อดูข้อความบอตแบบ real-time เหมือนใน terminal
            <br>หาก log ค้างนิ่งนาน → บอตอาจค้าง / แจ้ง dev
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:0.75rem;">
            ${BOTS.map(b => `
                <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1rem;">
                    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.4rem;">
                        <span style="font-size:1.4rem;">${b.icon}</span>
                        <div>
                            <div style="font-weight:600;font-size:0.95rem;">${b.name}</div>
                            <div style="font-size:0.7rem;color:var(--text-dim);font-family:var(--font-mono,monospace);">${b.container}</div>
                        </div>
                    </div>
                    <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.7rem;">${b.desc}</div>
                    <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
                        <button class="btn btn-sm btn-primary" onclick="openBotLogStream('${b.container}')">📋 ดู log สด</button>
                        <button class="btn btn-sm btn-outline" onclick="restartBotContainer('${b.container}')" title="restart container (ต้องยืนยัน)">♻️ Restart</button>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

async function restartBotContainer(container) {
    if (!await confirmModal({ message: '⚠️ Restart ' + container + '?\n\nบอตจะ offline ~5-10 วินาที\nลูกค้าที่กำลังใช้งานอาจเจอ timeout 1 ครั้ง\n\nยืนยัน?', dangerous: true })) return;
    try {
        const r = await api('/bots/' + encodeURIComponent(container) + '/restart', { method: 'POST' });
        toast('✅ ' + container + ' restarting...', 'success');
        setTimeout(() => loadBotsStatus(), 2000);
    } catch (e) {
        toast('❌ ' + e.message, 'error');
    }
}

// ==================================================================
// Phase A.4 (2026-06-27): Toggle clip_poster_bot permission per admin
// ==================================================================
// Legacy toggleClipPermission removed — use showBotPermsModal() instead

function botPermsBadge(memberId, displayName, role, canManage) {
    const isOwner = role === 'owner';
    const safeName = esc(displayName).replace(/'/g, "\\'");
    const onclick = (isOwner || !canManage)
        ? '' : `onclick="showBotPermsModal(${memberId}, '${safeName}')"`;
    const tooltip = isOwner
        ? 'Owner ใช้บอตได้ทุกตัว' : (canManage ? 'คลิกเพื่อจัดการสิทธิ์' : 'ต้องเป็น super_admin ขึ้นไปถึงจะปรับได้');
    return `<span class="btn btn-sm btn-outline" style="${isOwner || !canManage ? 'cursor:default;opacity:0.8;' : 'cursor:pointer;'}" ${onclick} title="${tooltip}" id="bot-perms-badge-${memberId}">⏳</span>`;
}

// On render: fetch each member's bot perms count and update badge text
async function loadBotPermsBadges() {
    const badges = document.querySelectorAll('[id^="bot-perms-badge-"]');
    for (const el of badges) {
        const memberId = el.id.replace('bot-perms-badge-', '');
        try {
            const data = await api(`/team/${memberId}/bot-permissions`);
            const total = data.bots.length;
            const granted = data.granted.length;
            const isOwner = data.member.role === 'owner';
            el.innerHTML = isOwner
                ? `🤖 ทั้งหมด (${total})`
                : `🤖 ${granted}/${total}`;
            // Color code
            if (granted === 0) el.style.color = 'var(--text-muted)';
            else if (granted === total) el.style.color = 'var(--success)';
            else el.style.color = 'var(--warning)';
        } catch (e) { el.innerHTML = '⚠️ err'; }
    }
}

async function showBotPermsModal(memberId, displayName) {
    try {
        const data = await api(`/team/${memberId}/bot-permissions`);
        const grantedSet = new Set(data.granted);
        const checkboxes = data.bots.map(b => {
            const checked = grantedSet.has(b.bot_key);
            return `<label style="display:flex;align-items:center;gap:0.6rem;padding:0.6rem;border-bottom:1px solid var(--border);cursor:pointer;">
                <input type="checkbox" data-bot-key="${esc(b.bot_key)}" ${checked ? 'checked' : ''} style="width:18px;height:18px;cursor:pointer;">
                <span style="font-size:1.3rem;">${b.icon}</span>
                <div style="flex:1;">
                    <div style="font-weight:600;">${esc(b.display_name)}</div>
                    <div style="font-size:0.75rem;color:var(--text-muted);">${esc(b.description || '')}</div>
                </div>
            </label>`;
        }).join('');
        openModal(`🤖 จัดการสิทธิ์ใช้บอท — ${esc(displayName)}`, `
            <div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.75rem;">
                เลือกบอทที่ <b>${esc(displayName)}</b> จะใช้งานได้
            </div>
            <div id="bot-perms-list" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
                ${checkboxes}
            </div>
            <div style="margin-top:1rem;display:flex;gap:0.5rem;justify-content:flex-end;">
                <button class="btn btn-outline" onclick="closeModal()">ยกเลิก</button>
                <button class="btn btn-primary" onclick="saveBotPerms(${memberId})">💾 บันทึก</button>
            </div>
        `);
    } catch (e) {
        toast('❌ ' + e.message, 'error');
    }
}

async function saveBotPerms(memberId) {
    const checkboxes = document.querySelectorAll('#bot-perms-list input[type="checkbox"]:checked');
    const bot_keys = Array.from(checkboxes).map(cb => cb.dataset.botKey);
    try {
        await api(`/team/${memberId}/bot-permissions`, {
            method: 'PATCH',
            body: JSON.stringify({ bot_keys }),
        });
        toast(`💾 บันทึกสิทธิ์ ${bot_keys.length} บอท สำเร็จ`, 'success');
        closeModal();
        if (typeof renderTeam === 'function') await renderTeam();
    } catch (e) {
        toast('❌ ' + e.message, 'error');
    }
}

// ==================================================================
// Phase A.6 (2026-06-27): Bot × Group management page
// ==================================================================
async function renderBotGroups() {
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const bots = await api("/admin/bots-registry");
        const gc = (g, role) => (g && g[role]) ? g[role] : 0;

        let html = `
            <div style="background:rgba(0,212,255,0.05);border:1px solid rgba(0,212,255,0.2);border-radius:8px;padding:0.75rem 1rem;margin-bottom:1rem;font-size:0.85rem;color:var(--text-muted);">
                💡 คลิกที่บอตเพื่อตั้งค่ากลุ่มที่บอตจะกระจาย/รับ/ตรวจสอบ
                <br>เพิ่มกลุ่มที่หน้า 📱 กลุ่ม VIP/ฟรี ก่อน แล้วกลับมาติ๊กที่นี่
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:0.75rem;">
        `;

        for (const bot of bots) {
            const dist = gc(bot.group_counts, 'distribution');
            const src = gc(bot.group_counts, 'source');
            const mon = gc(bot.group_counts, 'monitor');
            html += `
                <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1rem;cursor:pointer;transition:transform 0.1s;" onclick="openBotGroupsModal('${esc(bot.bot_key)}')" onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform=''">
                    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">
                        <span style="font-size:1.6rem;">${bot.icon || '🤖'}</span>
                        <div>
                            <div style="font-weight:600;font-size:1rem;">${esc(bot.display_name)}</div>
                            <div style="font-size:0.7rem;color:var(--text-dim);font-family:var(--font-mono);">${esc(bot.bot_key)}</div>
                        </div>
                    </div>
                    <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.7rem;">${esc(bot.description || '')}</div>
                    <div style="display:flex;gap:0.5rem;font-size:0.75rem;flex-wrap:wrap;">
                        ${src > 0 ? `<span style="background:rgba(34,197,94,0.15);color:var(--success);padding:0.2rem 0.5rem;border-radius:4px;">📥 ขาเข้า ${src}</span>` : ''}
                        ${dist > 0 ? `<span style="background:rgba(0,212,255,0.15);color:var(--primary);padding:0.2rem 0.5rem;border-radius:4px;">📤 ขาออก ${dist}</span>` : ''}
                        ${mon > 0 ? `<span style="background:rgba(247,176,69,0.15);color:var(--warning);padding:0.2rem 0.5rem;border-radius:4px;">👁 ตรวจ ${mon}</span>` : ''}
                        ${(src+dist+mon === 0) ? '<span style="color:var(--text-muted);">— ยังไม่มีกลุ่ม —</span>' : ''}
                    </div>
                </div>
            `;
        }
        html += '</div>';
        content.innerHTML = html;

        // Silent auto-refresh every 30s — refetch + repaint without spinner.
        // Only active while user stays on this page (cleared on next renderGroupAnalytics or nav).
        _gaAutoRefresh = setInterval(async () => {
            try {
                const fresh = await api('/admin/groups/analytics');
                // Detect if user is still on the analytics page
                const tbody = document.getElementById('analytics-tbody');
                if (!tbody) { clearInterval(_gaAutoRefresh); _gaAutoRefresh = null; return; }
                // Trigger a quiet redraw by calling renderGroupAnalytics() but skipping setInterval re-spawn
                _gaSilentRender(fresh);
            } catch (e) { /* silent */ }
        }, 30000);
    } catch (e) {
        content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    }
}

// Silent in-place repaint — avoids full-page spinner/flash
function _gaSilentRender(data) {
    _analyticsCache = data;
    const tbody = document.getElementById('analytics-tbody');
    if (!tbody) return;
    const fmtDelta = (v) => {
        if (v == null) return '<span style="color:var(--text-muted);">—</span>';
        if (v > 0) return `<span style="color:var(--success);">+${v.toLocaleString()}</span>`;
        if (v < 0) return `<span style="color:var(--error);">${v.toLocaleString()}</span>`;
        return `<span style="color:var(--text-muted);">0</span>`;
    };
    const fmtTrend = (d) => d > 5 ? '↗📈' : d > 0 ? '↗' : d < -5 ? '↘📉' : d < 0 ? '↘' : '→';
    const tierLabel = (t) => t === 'FREE' ? '🆓 ฟรี'
                          : t === 'TIER_100' ? '🔵 100'
                          : t === 'TIER_300' ? '👑 300'
                          : t === 'TIER_500' ? '👑 500'
                          : t === 'TIER_1299' ? '👑 1299'
                          : t === 'TIER_2499' ? '💎 2499' : t;

    let rowsHtml = '';
    data.forEach(r => {
        const tierClass = r.min_tier === 'FREE' ? 'free' : 'vip';
        rowsHtml += `<tr class="row-tier-${tierClass}">
            <td><b>${esc(r.slug)}</b><br><span style="font-size:0.75rem;color:var(--text-muted);">${esc(r.title)}</span></td>
            <td>${tierLabel(r.min_tier)}</td>
            <td style="text-align:right;font-weight:600;">${(r.current || 0).toLocaleString()}</td>
            <td style="text-align:right;">${fmtDelta(r.delta_day)}</td>
            <td style="text-align:right;">${fmtDelta(r.delta_week)}</td>
            <td style="text-align:right;">${fmtDelta(r.delta_month)}</td>
            <td style="font-size:1.1rem;">${fmtTrend(r.delta_day || 0)}</td>
            <td><button class="btn btn-sm btn-outline" onclick="showGroupChart(${r.chat_id}, '${esc(r.slug)}')" title="ดูกราฟ">📈</button></td>
        </tr>`;
    });
    tbody.innerHTML = rowsHtml;

    // Update top mini-cards too
    const totalNow = data.reduce((s, r) => s + (r.current || 0), 0);
    const totalDay = data.reduce((s, r) => s + (r.delta_day || 0), 0);
    const totalWeek = data.reduce((s, r) => s + (r.delta_week || 0), 0);
    const totalMonth = data.reduce((s, r) => s + (r.delta_month || 0), 0);
    const cardVals = document.querySelectorAll('.mini-card-value');
    if (cardVals.length >= 4) {
        cardVals[0].textContent = totalNow.toLocaleString();
        cardVals[1].textContent = (totalDay >= 0 ? '+' : '') + totalDay.toLocaleString();
        cardVals[1].style.color = totalDay >= 0 ? 'var(--success)' : 'var(--error)';
        cardVals[2].textContent = (totalWeek >= 0 ? '+' : '') + totalWeek.toLocaleString();
        cardVals[2].style.color = totalWeek >= 0 ? 'var(--success)' : 'var(--error)';
        cardVals[3].textContent = (totalMonth >= 0 ? '+' : '') + totalMonth.toLocaleString();
        cardVals[3].style.color = totalMonth >= 0 ? 'var(--success)' : 'var(--error)';
    }

    // Update "snapshot ล่าสุด" caption
    const lastSnap = data.find(r => r.last_snapshot)?.last_snapshot;
    if (lastSnap) {
        const cap = document.querySelector('#page-content [data-last-snapshot]');
        if (cap) cap.textContent = 'snapshot ล่าสุด: ' + new Date(lastSnap).toLocaleString('th-TH');
    }
}

function _bgmRenderTier(tierName, groups, role, targets) {
    const heading = tierName === 'FREE' ? '🆓 กลุ่มฟรี'
                  : tierName.startsWith('TIER_') ? `👑 ${tierName.replace('TIER_', 'VIP ')}` : tierName;
    return `<div style="margin-bottom:0.5rem;">
        <div style="font-weight:600;font-size:0.8rem;margin-bottom:0.3rem;color:var(--text-dim);">${heading}</div>
        ${groups.map(g => {
            const roles = targets[g.chat_id] || [];
            const checked = roles.includes(role);
            return `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem;border-bottom:1px solid var(--border);cursor:pointer;">
                <input type="checkbox" data-chat-id="${g.chat_id}" data-role="${role}" ${checked ? 'checked' : ''} style="width:16px;height:16px;cursor:pointer;">
                <span style="font-size:0.85rem;flex:1;"><b>${esc(g.slug)}</b> — ${esc(g.title)}</span>
                <span style="font-family:var(--font-mono);color:var(--text-dim);font-size:0.7rem;">${g.chat_id}</span>
            </label>`;
        }).join('')}
    </div>`;
}

function _bgmRenderPanel(allGroups, role, targets) {
    const byTier = {};
    allGroups.forEach(g => {
        const t = g.min_tier || 'OTHER';
        byTier[t] = byTier[t] || [];
        byTier[t].push(g);
    });
    let html = '';
    const order = ['FREE', 'TIER_100', 'TIER_300', 'TIER_500', 'TIER_1299', 'TIER_2499'];
    order.forEach(t => { if (byTier[t]) html += _bgmRenderTier(t, byTier[t], role, targets); });
    Object.keys(byTier).forEach(t => { if (!order.includes(t)) html += _bgmRenderTier(t, byTier[t], role, targets); });
    return html;
}

async function openBotGroupsModal(botKey) {
    try {
        const data = await api(`/admin/bots/${encodeURIComponent(botKey)}/groups`);
        const bot = data.bot;
        const defaultRole = botKey === 'guardian_bot' ? 'monitor' : 'distribution';
        const tabsHtml = botKey === 'relay_bot'
            ? `<div class="tabs" style="margin-bottom:0.6rem;">
                <div class="tab active" data-role="distribution" onclick="switchBotGroupTab(this)">📤 ปลายทาง</div>
                <div class="tab" data-role="source" onclick="switchBotGroupTab(this)">📥 ต้นทาง</div>
              </div>`
            : botKey === 'guardian_bot'
            ? `<div class="tabs" style="margin-bottom:0.6rem;">
                <div class="tab active" data-role="monitor" onclick="switchBotGroupTab(this)">👁 ตรวจสอบ</div>
                <div class="tab" data-role="distribution" onclick="switchBotGroupTab(this)">📤 ปลายทาง</div>
              </div>`
            : '';
        const panelHtml = _bgmRenderPanel(data.all_groups, defaultRole, data.targets);

        openModal(`${bot.icon || '🤖'} จัดการกลุ่ม — ${esc(bot.display_name)}`, `
            <div style="font-size:0.82rem;color:var(--text-muted);margin-bottom:0.75rem;">${esc(bot.description || '')}</div>
            ${tabsHtml}
            <div id="bot-groups-panel" data-bot-key="${esc(bot.bot_key)}" data-role="${defaultRole}" style="max-height:55vh;overflow-y:auto;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:0.5rem;">
                ${panelHtml}
            </div>
            <div style="margin-top:1rem;display:flex;gap:0.5rem;justify-content:flex-end;">
                <button class="btn btn-outline" onclick="closeModal()">ยกเลิก</button>
                <button class="btn btn-primary" onclick="saveBotGroups()">💾 บันทึก</button>
            </div>
        `);
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function switchBotGroupTab(el) {
    const role = el.dataset.role;
    const panel = document.getElementById('bot-groups-panel');
    if (!panel) return;
    const botKey = panel.dataset.botKey;
    document.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    panel.dataset.role = role;
    try {
        const data = await api(`/admin/bots/${encodeURIComponent(botKey)}/groups`);
        panel.innerHTML = _bgmRenderPanel(data.all_groups, role, data.targets);
    } catch (e) { toast(e.message, 'error'); }
}

async function saveBotGroups() {
    const panel = document.getElementById('bot-groups-panel');
    if (!panel) return;
    const botKey = panel.dataset.botKey;
    const role = panel.dataset.role || 'distribution';
    const checkboxes = panel.querySelectorAll('input[type="checkbox"]:checked');
    const chat_ids = Array.from(checkboxes).map(cb => parseInt(cb.dataset.chatId, 10)).filter(x => !isNaN(x));
    try {
        await api(`/admin/bots/${encodeURIComponent(botKey)}/groups`, {
            method: 'PATCH',
            body: JSON.stringify({ target_role: role, chat_ids }),
        });
        toast(`💾 บันทึก ${chat_ids.length} กลุ่ม (${role})`, 'success');
        closeModal();
        if (typeof renderBotGroups === 'function') await renderBotGroups();
    } catch (e) { toast(e.message, 'error'); }
}

// ==================================================================
// Phase A.7 (2026-06-27): Group Member Analytics page
// ==================================================================
var _analyticsCache = null;

var _gaAutoRefresh = null;
async function renderGroupAnalytics() {
    // Clear any existing auto-refresh on this re-entry
    if (_gaAutoRefresh) { clearInterval(_gaAutoRefresh); _gaAutoRefresh = null; }
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const data = await api('/admin/groups/analytics');
        _analyticsCache = data;

        // Summary
        const totalNow = data.reduce((s, r) => s + (r.current || 0), 0);
        const totalDay = data.reduce((s, r) => s + (r.delta_day || 0), 0);
        const totalWeek = data.reduce((s, r) => s + (r.delta_week || 0), 0);
        const totalMonth = data.reduce((s, r) => s + (r.delta_month || 0), 0);

        const fmtDelta = (v) => {
            if (v == null) return '<span style="color:var(--text-muted);">—</span>';
            if (v > 0) return `<span style="color:var(--success);">+${v.toLocaleString()}</span>`;
            if (v < 0) return `<span style="color:var(--error);">${v.toLocaleString()}</span>`;
            return `<span style="color:var(--text-muted);">0</span>`;
        };
        const fmtTrend = (d, w, m) => {
            const recent = d;
            if (recent > 5) return '↗📈';
            if (recent > 0) return '↗';
            if (recent < -5) return '↘📉';
            if (recent < 0) return '↘';
            return '→';
        };
        const tierLabel = (t) => t === 'FREE' ? '🆓 ฟรี'
                              : t === 'TIER_100' ? '🔵 100'
                              : t === 'TIER_300' ? '👑 300'
                              : t === 'TIER_500' ? '👑 500'
                              : t === 'TIER_1299' ? '👑 1299'
                              : t === 'TIER_2499' ? '💎 2499' : t;

        const lastSnapshot = data.find(r => r.last_snapshot)?.last_snapshot;
        const lastText = lastSnapshot ? new Date(lastSnapshot).toLocaleString('th-TH') : '—';

        let html = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem;">
                <div>
                    <h2 style="margin:0;font-size:1.2rem;">📊 สถิติกลุ่ม</h2>
                    <div style="font-size:0.75rem;color:var(--text-muted);display:flex;align-items:center;gap:0.4rem;">
                        <span style="display:inline-block;width:8px;height:8px;background:#10b981;border-radius:50%;animation:gaPulse 2s infinite;"></span>
                        <span data-last-snapshot>snapshot ล่าสุด: ${lastText}</span>
                        <span style="opacity:0.6;">· auto-update ทุก 30 วิ</span>
                    </div>
                </div>
                <div style="display:flex;gap:0.5rem;">
                    <button class="btn btn-outline btn-sm" onclick="exportGroupAnalytics()">📥 Export Excel</button>
                </div>
            </div>
            <style>@keyframes gaPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }</style>

            <div class="mini-cards">
                <div class="mini-card"><div class="mini-card-label">รวมทั้งหมด</div><div class="mini-card-value" style="color:var(--primary);">${totalNow.toLocaleString()}</div></div>
                <div class="mini-card"><div class="mini-card-label">+ วันนี้</div><div class="mini-card-value" style="color:${totalDay >= 0 ? 'var(--success)' : 'var(--error)'};">${totalDay >= 0 ? '+' : ''}${totalDay.toLocaleString()}</div></div>
                <div class="mini-card"><div class="mini-card-label">+ สัปดาห์</div><div class="mini-card-value" style="color:${totalWeek >= 0 ? 'var(--success)' : 'var(--error)'};">${totalWeek >= 0 ? '+' : ''}${totalWeek.toLocaleString()}</div></div>
                <div class="mini-card"><div class="mini-card-label">+ เดือน</div><div class="mini-card-value" style="color:${totalMonth >= 0 ? 'var(--success)' : 'var(--error)'};">${totalMonth >= 0 ? '+' : ''}${totalMonth.toLocaleString()}</div></div>
            </div>

            <div class="tabs" style="margin-top:1rem;margin-bottom:0.5rem;">
                <div class="tab active" data-filter="all" onclick="filterAnalyticsTab(this)">📑 ทั้งหมด (${data.length})</div>
                <div class="tab" data-filter="FREE" onclick="filterAnalyticsTab(this)">🆓 ฟรี (${data.filter(r=>r.min_tier==='FREE').length})</div>
                <div class="tab" data-filter="VIP" onclick="filterAnalyticsTab(this)">👑 VIP (${data.filter(r=>r.min_tier!=='FREE').length})</div>
            </div>

            <div class="table-wrap"><table><thead><tr>
                <th>กลุ่ม</th><th>Tier</th><th style="text-align:right;">ตอนนี้</th>
                <th style="text-align:right;">+ วัน</th><th style="text-align:right;">+ สัปดาห์</th><th style="text-align:right;">+ เดือน</th>
                <th>แนวโน้ม</th><th></th>
            </tr></thead><tbody id="analytics-tbody">
        `;

        data.forEach(r => {
            const tierClass = r.min_tier === 'FREE' ? 'free' : 'vip';
            html += `<tr class="row-tier-${tierClass}">
                <td><b>${esc(r.slug)}</b><br><span style="font-size:0.75rem;color:var(--text-muted);">${esc(r.title)}</span></td>
                <td>${tierLabel(r.min_tier)}</td>
                <td style="text-align:right;font-weight:600;">${(r.current || 0).toLocaleString()}</td>
                <td style="text-align:right;">${fmtDelta(r.delta_day)}</td>
                <td style="text-align:right;">${fmtDelta(r.delta_week)}</td>
                <td style="text-align:right;">${fmtDelta(r.delta_month)}</td>
                <td style="font-size:1.1rem;">${fmtTrend(r.delta_day, r.delta_week, r.delta_month)}</td>
                <td><button class="btn btn-sm btn-outline" onclick="showGroupChart(${r.chat_id}, '${esc(r.slug)}')" title="ดูกราฟ">📈</button></td>
            </tr>`;
        });
        html += '</tbody></table></div>';

        if (data.every(r => r.last_snapshot == null)) {
            html += `<div style="margin-top:1rem;padding:1rem;background:rgba(247,176,69,0.1);border:1px solid var(--warning);border-radius:8px;font-size:0.85rem;">
                ⏳ ระบบกำลัง snapshot กลุ่มทั้งหมดในรอบแรก (~30 วินาที)
                <br>หน้านี้จะอัปเดตอัตโนมัติเมื่อมีข้อมูลเข้ามา
            </div>`;
        }

        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    }
}

function filterAnalyticsTab(el) {
    const filter = el.dataset.filter;
    document.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    const rows = document.querySelectorAll('#analytics-tbody tr');
    rows.forEach(r => {
        if (filter === 'all') r.style.display = '';
        else if (filter === 'FREE') r.style.display = r.classList.contains('row-tier-free') ? '' : 'none';
        else if (filter === 'VIP') r.style.display = r.classList.contains('row-tier-vip') ? '' : 'none';
    });
}

async function refreshSnapshots() {
    const btn = event?.target;
    if (btn) { btn.disabled = true; btn.textContent = '⏳ กำลัง snapshot...'; }
    try {
        const r = await api('/admin/snapshot-group-members', { method: 'POST' });
        toast(`✅ snapshot ${r.snapshotted}/${r.total_groups} กลุ่ม${r.failed.length ? ` (fail ${r.failed.length})` : ''}`, 'success');
        await renderGroupAnalytics();
    } catch (e) { toast(e.message, 'error'); }
    finally { if (btn) { btn.disabled = false; } }
}

async function showGroupChart(chatId, slug) {
    try {
        const data = await api(`/admin/groups/${chatId}/timeseries?days=30`);
        if (!data.length) {
            openModal('📈 กราฟ', '<div class="empty-state">ยังไม่มี snapshot สำหรับกลุ่มนี้</div>');
            return;
        }
        // Simple ASCII-like SVG sparkline
        const maxN = Math.max(...data.map(d => d.n));
        const minN = Math.min(...data.map(d => d.n));
        const range = maxN - minN || 1;
        const points = data.map((d, i) => {
            const x = (i / (data.length - 1)) * 600 + 20;
            const y = 180 - ((d.n - minN) / range) * 160 + 10;
            return `${x},${y}`;
        }).join(' ');
        openModal(`📈 ${esc(slug)} — กราฟ 30 วัน`, `
            <div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.5rem;">
                ${data.length} snapshot · จุดต่ำสุด ${minN} → สูงสุด ${maxN}
            </div>
            <svg viewBox="0 0 640 200" style="width:100%;background:var(--surface);border:1px solid var(--border);border-radius:6px;">
                <polyline points="${points}" fill="none" stroke="var(--primary)" stroke-width="2" />
                ${data.map((d, i) => {
                    const x = (i / (data.length - 1)) * 600 + 20;
                    const y = 180 - ((d.n - minN) / range) * 160 + 10;
                    return `<circle cx="${x}" cy="${y}" r="3" fill="var(--primary)"><title>${new Date(d.t).toLocaleString('th-TH')} — ${d.n}</title></circle>`;
                }).join('')}
            </svg>
        `);
    } catch (e) { toast(e.message, 'error'); }
}

function exportGroupAnalytics() {
    if (!_analyticsCache) { toast('โหลดข้อมูลก่อน', 'warning'); return; }
    const rows = [['กลุ่ม', 'ชื่อ', 'Tier', 'ตอนนี้', '+วัน', '+สัปดาห์', '+เดือน']];
    _analyticsCache.forEach(r => {
        rows.push([r.slug, r.title, r.min_tier, r.current || 0, r.delta_day || 0, r.delta_week || 0, r.delta_month || 0]);
    });
    const csv = rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `group-analytics-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
}

// ==================================================================
// Phase A.8 (2026-06-27): Schedule Manager page
// ==================================================================
var _schedBot = 'content_bot';

async function renderBotSchedules() {
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        // Bot tabs (just content_bot for now; future bots can be added)
        const bots = await api('/admin/bots-registry');
        const tabsHtml = bots.map(b => `
            <button class="ga2-seg-btn ${_schedBot===b.bot_key?'active':''}" onclick="_schedBot='${b.bot_key}';renderBotSchedules()">${b.icon||'🤖'} ${esc(b.display_name)}</button>
        `).join('');

        const schedules = await api(`/admin/bots/${encodeURIComponent(_schedBot)}/schedules`);
        if (!schedules.length) {
            content.innerHTML = `<div class="ga2-seg" style="margin-bottom:1rem;">${tabsHtml}</div>
                <div class="empty-state">ยังไม่มี schedule สำหรับบอตนี้</div>`;
            return;
        }

        // Group by category
        const byCat = {};
        schedules.forEach(s => { (byCat[s.category] = byCat[s.category] || []).push(s); });
        const catLabel = (c) => ({teaser:'📸 Teaser',promo:'🎁 Promo',system:'⚙️ Internal'})[c] || c;

        let html = `
        <style>
          .ga2-seg{display:inline-flex;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:2px;}
          .ga2-seg-btn{background:transparent;border:none;padding:0.3rem 0.75rem;font-size:0.78rem;border-radius:6px;cursor:pointer;color:var(--text-muted);font-weight:500;}
          .ga2-seg-btn:hover{color:var(--text);}
          .ga2-seg-btn.active{background:var(--primary);color:#000;font-weight:600;}
          .sched-section{margin-bottom:1.2rem;}
          .sched-section-title{font-size:0.75rem;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.5rem;}
          .sched-row{display:grid;grid-template-columns:auto 1fr auto auto;gap:0.75rem;align-items:center;padding:0.7rem 1rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;margin-bottom:0.5rem;}
          /* Toggle switch — label wrapper because input::before doesnt work */
          .sched-switch{position:relative;display:inline-block;width:44px;height:24px;flex-shrink:0;}
          .sched-switch input{opacity:0;width:0;height:0;margin:0;position:absolute;}
          .sched-slider{position:absolute;top:0;left:0;right:0;bottom:0;background:#3f3f46;border-radius:24px;cursor:pointer;transition:background 0.2s;box-shadow:inset 0 1px 2px rgba(0,0,0,0.2);}
          .sched-slider::before{content:"";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:transform 0.2s;box-shadow:0 1px 3px rgba(0,0,0,0.4);}
          .sched-switch input:checked + .sched-slider{background:#10b981;}
          .sched-switch input:checked + .sched-slider::before{transform:translateX(20px);}
          .sched-time-input{display:inline-flex;gap:0.15rem;align-items:center;background:#27272a;border:1px solid #3f3f46;border-radius:6px;padding:0.3rem 0.55rem;}
          .sched-time-input input{background:#3f3f46;border:1px solid #52525b;border-radius:4px;color:#fff;font-size:0.95rem;font-weight:600;width:42px;height:28px;text-align:center;font-variant-numeric:tabular-nums;padding:0;margin:0;}
          .sched-time-input input:focus{outline:none;border-color:#10b981;}
          .sched-time-input input::-webkit-inner-spin-button,.sched-time-input input::-webkit-outer-spin-button{-webkit-appearance:none;margin:0;}
          .sched-time-input span{color:#9ca3af;font-weight:600;font-size:1rem;}
          .sched-disabled{opacity:0.5;}
        </style>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem;">
            <div>
                <h2 style="margin:0;font-size:1.2rem;">⏰ ตารางเวลาบอท</h2>
                <div style="font-size:0.75rem;color:var(--text-muted);">เปิด/ปิด job และแก้เวลาได้ทันที</div>
            </div>
            <div style="display:flex;gap:0.5rem;align-items:center;">
                <div class="ga2-seg">${tabsHtml}</div>
                <button class="btn btn-primary" onclick="schedOpenAdd()" style="white-space:nowrap;">➕ เพิ่มตารางเวลา</button>
            </div>
        </div>
        `;

        const orderCats = ['teaser', 'promo', 'system'];
        const renderCat = (cat) => {
            const items = byCat[cat] || [];
            if (!items.length) return '';
            return `<div class="sched-section">
                <div class="sched-section-title">${catLabel(cat)}</div>
                ${items.map(s => `
                    <div class="sched-row ${s.is_enabled?'':'sched-disabled'}" id="sched-row-${s.id}">
                        <label class="sched-switch">
                            <input type="checkbox" ${s.is_enabled?'checked':''} onchange="toggleSchedule(${s.id}, this.checked)">
                            <span class="sched-slider"></span>
                        </label>
                        <div>
                            <div style="font-weight:600;font-size:0.9rem;">${esc(s.display_name)}</div>
                            <div style="font-size:0.72rem;color:var(--text-muted);">${esc(s.description || '')}</div>
                        </div>
                        <div class="sched-time-input">
                            <input type="number" min="0" max="23" value="${String(s.schedule_hour).padStart(2,'0')}" data-sched="${s.id}" data-field="hour" onchange="updateSchedTime(${s.id},'hour',this.value)">
                            <span style="color:var(--text-muted);">:</span>
                            <input type="number" min="0" max="59" value="${String(s.schedule_minute).padStart(2,'0')}" data-sched="${s.id}" data-field="minute" onchange="updateSchedTime(${s.id},'minute',this.value)">
                        </div>
                        <div style="font-size:0.7rem;color:var(--text-muted);font-family:var(--font-mono);">${esc(s.job_name)}</div>
                    </div>
                `).join('')}
            </div>`;
        };
        orderCats.forEach(c => { html += renderCat(c); });
        Object.keys(byCat).forEach(c => { if (!orderCats.includes(c)) html += renderCat(c); });

        html += `<div style="margin-top:1rem;padding:0.85rem 1rem;background:rgba(247,176,69,0.1);border:1px solid var(--warning);border-radius:8px;font-size:0.8rem;color:var(--text-muted);">
            💡 <b>หมายเหตุ:</b> เปิด/ปิด toggle = มีผลภายใน 1 นาที (cache).
            แก้เวลา = มีผลรอบถัดไปหลัง restart บอต (job_queue ต้อง re-register).
        </div>`;

        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    }
}

async function toggleSchedule(schedId, enabled) {
    try {
        await api(`/admin/bots/schedules/${schedId}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_enabled: enabled }),
        });
        toast(`${enabled?'✅':'⏸'} ${enabled?'เปิด':'ปิด'} job สำเร็จ`, 'success');
        const row = document.getElementById('sched-row-' + schedId);
        if (row) row.classList.toggle('sched-disabled', !enabled);
    } catch (e) { toast(e.message, 'error'); }
}

async function updateSchedTime(schedId, field, value) {
    const v = parseInt(value, 10);
    if (isNaN(v)) return;
    try {
        await api(`/admin/bots/schedules/${schedId}`, {
            method: 'PATCH',
            body: JSON.stringify({ ['schedule_' + field]: v }),
        });
        toast('💾 บันทึกเวลา · บอตจะใช้รอบถัดไปหลัง restart', 'success');
    } catch (e) { toast(e.message, 'error'); }
}

// ==================================================================
// Phase B.1.B (2026-06-27): Content Editor page
// ==================================================================
const CT_EMOJIS = [
    "🔥","💎","💰","💸","✨","⭐","🎉","🎁","🎰","🎲","👑","💕","❤️","💯","🚀","⚡",
    "✅","❌","👉","👈","👀","🥵","🍑","🌶","🍒","🌸","💋","😍","😘","😊","😎","🤤",
    "📢","📌","📍","🏷","💬","💌","🔔","📲","📱","💻","🎬","📸","🎥","🎞","📷","🖼"
];

function ctInsertAt(textareaEl, before, after) {
    if (!textareaEl) return;
    const start = textareaEl.selectionStart;
    const end = textareaEl.selectionEnd;
    const text = textareaEl.value;
    const selected = text.slice(start, end);
    textareaEl.value = text.slice(0, start) + before + selected + after + text.slice(end);
    textareaEl.focus();
    const newPos = start + before.length + selected.length + (selected.length === 0 ? 0 : after.length);
    textareaEl.selectionStart = textareaEl.selectionEnd = selected.length === 0 ? start + before.length : newPos;
    textareaEl.dispatchEvent(new Event("input"));
}

function ctTextareaFor(btn) {
    return btn.closest(".ct-card").querySelector("[data-field='caption_html']");
}

function ctImageInputFor(btn) {
    return btn.closest(".ct-card").querySelector("[data-field='image_path']");
}

function ctFormat(btn, tag) {
    ctInsertAt(ctTextareaFor(btn), "<" + tag + ">", "</" + tag + ">");
}

function ctInsertText(btn, text) {
    ctInsertAt(ctTextareaFor(btn), text, "");
}

function ctInsertLink(btn) {
    const url = prompt("ใส่ URL ลิ้ง:", "https://t.me/NamwarnJarern_bot");
    if (!url) return;
    const label = prompt("ข้อความที่ลูกค้าเห็น:", "กดที่นี่") || url;
    const ta = ctTextareaFor(btn);
    ctInsertAt(ta, '<a href="' + url + '">' + label + '</a>', "");
}

function ctInsertDivider(btn) {
    ctInsertText(btn, "\n━━━━━━━━━━━━━━━━━━\n");
}

function ctOpenEmoji(btn) {
    // Toggle existing popover if any
    document.querySelectorAll(".ct-emoji-popover").forEach(p => p.remove());
    const ta = ctTextareaFor(btn);
    const pop = document.createElement("div");
    pop.className = "ct-emoji-popover";
    pop.style.cssText = "position:absolute;background:#27272a;border:1px solid #3f3f46;border-radius:8px;padding:0.4rem;display:grid;grid-template-columns:repeat(8,1fr);gap:0.2rem;z-index:1000;box-shadow:0 4px 16px rgba(0,0,0,0.4);";
    CT_EMOJIS.forEach(emoji => {
        const b = document.createElement("button");
        b.textContent = emoji;
        b.type = "button";
        b.style.cssText = "background:transparent;border:none;font-size:1.2rem;cursor:pointer;padding:0.25rem;border-radius:4px;";
        b.onmouseover = () => b.style.background = "#3f3f46";
        b.onmouseout = () => b.style.background = "transparent";
        b.onclick = (e) => {
            e.stopPropagation();
            ctInsertAt(ta, emoji, "");
            pop.remove();
        };
        pop.appendChild(b);
    });
    const rect = btn.getBoundingClientRect();
    pop.style.left = (rect.left + window.scrollX) + "px";
    pop.style.top = (rect.bottom + window.scrollY + 4) + "px";
    document.body.appendChild(pop);
    setTimeout(() => {
        const close = (ev) => {
            if (!pop.contains(ev.target) && ev.target !== btn) {
                pop.remove();
                document.removeEventListener("click", close);
            }
        };
        document.addEventListener("click", close);
    }, 50);
}

async function ctUploadImage(btn) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp,image/gif";
    input.onchange = async (e) => {
        const f = e.target.files[0];
        if (!f) return;
        if (f.size > 10 * 1024 * 1024) { toast("ไฟล์ใหญ่เกิน 10 MB", "error"); return; }
        const fd = new FormData();
        fd.append("file", f);
        btn.disabled = true;
        const oldText = btn.textContent;
        btn.textContent = "⏳ กำลังอัพ...";
        try {
            const r = await fetch("/api/admin/upload-content-image", {
                method: "POST",
                headers: { "Authorization": "Bearer " + (typeof token !== "undefined" ? token : localStorage.getItem("jwt") || "") },
                body: fd,
            });
            if (!r.ok) throw new Error("upload failed: " + r.status);
            const j = await r.json();
            const imgEl = ctImageInputFor(btn);
            if (imgEl) imgEl.value = j.path;
            toast("📷 อัพโหลดสำเร็จ: " + j.path, "success");
        } catch (err) { toast(err.message, "error"); }
        finally { btn.disabled = false; btn.textContent = oldText; }
    };
    input.click();
}

function ctPreview(btn) {
    const ta = ctTextareaFor(btn);
    if (!ta) return;
    const html = ta.value;  // HTML — render directly (Telegram allows <b><i><u><s><a>)
    const imgPath = (ctImageInputFor(btn) || {}).value || "";
    const card = btn.closest(".ct-card");
    const btns = ctCollectButtons(card);

    // Sample placeholder fill: substitute common placeholders for preview
    const renderHtml = html.replace(/\{available\}/g, "8")
                           .replace(/\{tier\}/g, "300");

    // Build image URL via backend asset endpoint
    const imgSrc = imgPath
        ? "/api/admin/asset?path=" + encodeURIComponent(imgPath) + "&token=" + encodeURIComponent(typeof token !== "undefined" ? token : (localStorage.getItem("jwt") || ""))
        : null;

    // Build buttons HTML — Telegram inline button style: full-width grey pill rows
    const buttonsHtml = (btns && btns.length)
        ? btns.map(b =>
            `<div style="background:rgba(255,255,255,0.06);border-top:1px solid rgba(255,255,255,0.1);padding:0.65rem;color:#5eb6f5;text-align:center;font-size:0.88rem;font-weight:500;cursor:pointer;">${esc(b.label)}</div>`
          ).join("")
        : "";

    openModal("👁 ตัวอย่างใน Telegram", `
        <div style="background:#0e1621;padding:1rem;border-radius:10px;display:flex;justify-content:flex-start;">
            <div style="max-width:480px;width:100%;">
                <!-- Telegram message bubble (incoming style) -->
                <div style="display:flex;gap:0.5rem;align-items:flex-start;">
                    <div style="width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#ec4899,#f97316);display:flex;align-items:center;justify-content:center;color:#fff;font-size:0.95rem;font-weight:600;flex-shrink:0;">มิน</div>
                    <div style="flex:1;">
                        <div style="background:#182533;color:#fff;border-radius:12px;border-top-left-radius:4px;overflow:hidden;font-family:system-ui,-apple-system,sans-serif;">
                            <div style="padding:0.4rem 0.75rem 0.2rem;font-size:0.78rem;color:#5eb6f5;font-weight:600;">มิน 🎬</div>
                            ${imgSrc ? `<div style="background:#0e1621;"><img src="${imgSrc}" style="display:block;width:100%;max-height:400px;object-fit:cover;" onerror="this.style.display='none';this.parentElement.innerHTML='<div style=padding:1rem;color:#888;text-align:center;font-size:0.8rem;>⚠️ โหลดรูปไม่ได้: ${esc(imgPath)}</div>';"/></div>` : ""}
                            <div style="padding:0.6rem 0.75rem 0.5rem;font-size:0.92rem;line-height:1.55;color:#fff;white-space:pre-wrap;word-wrap:break-word;" class="tg-caption">${renderHtml}</div>
                            ${buttonsHtml}
                            <div style="padding:0.2rem 0.75rem 0.4rem;text-align:right;font-size:0.7rem;color:#7a8b9a;">${new Date().toLocaleTimeString("th-TH",{hour:"2-digit",minute:"2-digit"})} ✓✓</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <div style="margin-top:0.6rem;font-size:0.75rem;color:var(--text-muted);text-align:center;">
            ↑ คือสิ่งที่ลูกค้าจะเห็นในกลุ่ม (placeholder ใช้ค่าตัวอย่าง: available=8, tier=300)
        </div>
        <style>
            .tg-caption a { color: #5eb6f5; text-decoration: none; }
            .tg-caption a:hover { text-decoration: underline; }
            .tg-caption b, .tg-caption strong { font-weight: 700; }
            .tg-caption i, .tg-caption em { font-style: italic; }
            .tg-caption u { text-decoration: underline; }
            .tg-caption s, .tg-caption del { text-decoration: line-through; opacity: 0.7; }
            .tg-caption code { background: rgba(255,255,255,0.1); padding: 1px 4px; border-radius: 3px; font-family: monospace; font-size: 0.85em; }
        </style>
    `, "", "wide");
}
var _ctTab = 'promo';

async function renderContentEditor() {
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const all = await api('/admin/content-templates');
        const promos = all.filter(t => t.category === 'promo');
        const styles = all.filter(t => t.category === 'teaser_style');

        let html = `
        <style>
          .ct-tabs{display:inline-flex;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:2px;margin-bottom:1rem;}
          .ct-tab{background:transparent;border:none;padding:0.4rem 1rem;font-size:0.85rem;border-radius:6px;cursor:pointer;color:var(--text-muted);font-weight:500;}
          .ct-tab.active{background:var(--primary);color:#000;font-weight:600;}
          .ct-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1rem;margin-bottom:0.75rem;}
          .ct-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.6rem;gap:0.5rem;}
          .ct-title{font-weight:600;font-size:0.95rem;}
          .ct-desc{font-size:0.75rem;color:var(--text-muted);margin-top:0.15rem;}
          .ct-label{font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.3rem;display:block;}
          .ct-textarea{width:100%;min-height:120px;background:#27272a;color:#fff;border:1px solid #3f3f46;border-radius:6px;padding:0.6rem;font-family:var(--font-mono,monospace);font-size:0.8rem;resize:vertical;}
          .ct-textarea:focus{outline:none;border-color:var(--primary);}
          .ct-input{width:100%;background:#27272a;color:#fff;border:1px solid #3f3f46;border-radius:6px;padding:0.45rem 0.6rem;font-size:0.85rem;}
          .ct-input:focus{outline:none;border-color:var(--primary);}
          .ct-actions{display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.7rem;}
          .ct-toolbar{display:flex;gap:0.25rem;align-items:center;background:#1c1c1f;border:1px solid #3f3f46;border-bottom:none;border-radius:6px 6px 0 0;padding:0.35rem 0.45rem;flex-wrap:wrap;margin-top:0.2rem;}
          .ct-toolbar button{background:#27272a;border:1px solid #3f3f46;color:#fff;padding:0.2rem 0.55rem;border-radius:4px;cursor:pointer;font-size:0.8rem;display:inline-flex;align-items:center;gap:0.25rem;}
          .ct-toolbar button:hover{background:#3f3f46;border-color:#52525b;}
          .ct-tb-sep{color:#52525b;margin:0 0.15rem;}
          .ct-card .ct-toolbar + .ct-textarea{border-top-left-radius:0;border-top-right-radius:0;border-top-color:#3f3f46;}
        </style>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem;">
            <div>
                <h2 style="margin:0;font-size:1.2rem;">📝 คอนเทนต์ที่บอตจะโพสต์</h2>
                <div style="font-size:0.75rem;color:var(--text-muted);">แก้ข้อความ + รูป — บอตจะใช้ทันทีในรอบโพสต์ถัดไป</div>
            </div>
            <button class="btn btn-primary" onclick="ctOpenAddTemplate()" style="white-space:nowrap;">➕ เพิ่มเทมเพลต</button>
        </div>

        <div class="ct-tabs">
            <button class="ct-tab ${_ctTab==='promo'?'active':''}" onclick="_ctTab='promo';renderContentEditor()">🎁 โปรโมชั่น (${promos.length})</button>
            <button class="ct-tab ${_ctTab==='teaser_style'?'active':''}" onclick="_ctTab='teaser_style';renderContentEditor()">📸 สไตล์ตัวอย่าง (${styles.length})</button>
        </div>

        <div id="ct-list">
        `;

        const items = _ctTab === 'promo' ? promos : styles;
        items.forEach(t => {
            const safeName = esc(t.display_name);
            const safeDesc = esc(t.description);
            const isPromo = _ctTab === 'promo';
            html += `
              <div class="ct-card" data-tpl="${t.id}">
                <div class="ct-head">
                    <div>
                        <div class="ct-title">${safeName}</div>
                        <div class="ct-desc">${safeDesc}</div>
                    </div>
                    <span style="font-family:var(--font-mono);font-size:0.7rem;color:var(--text-dim);">${esc(t.template_key)}</span>
                </div>

                <label class="ct-label">${isPromo ? 'ข้อความ caption (HTML รองรับ &lt;b&gt;)' : 'AI Prompt (บอกแนวให้ AI ใช้สร้าง caption)'}</label>
                ${isPromo ? `<div class="ct-toolbar">
                    <button type="button" onclick="ctFormat(this,'b')" title="ตัวหนา"><b>B</b></button>
                    <button type="button" onclick="ctFormat(this,'i')" title="ตัวเอียง"><i>I</i></button>
                    <button type="button" onclick="ctFormat(this,'u')" title="ขีดเส้นใต้"><u>U</u></button>
                    <button type="button" onclick="ctFormat(this,'s')" title="ขีดทับ"><s>S</s></button>
                    <span class="ct-tb-sep">|</span>
                    <button type="button" onclick="ctInsertLink(this)" title="ลิ้ง">🔗 ลิ้ง</button>
                    <button type="button" onclick="ctInsertDivider(this)" title="เส้นคั่น">〰️ เส้นคั่น</button>
                    <button type="button" onclick="ctOpenEmoji(this)" title="อีโมจิ">😊 อีโมจิ</button>
                    <span class="ct-tb-sep">|</span>
                    <button type="button" onclick="ctPreview(this)" title="ดูตัวอย่าง">👁 Preview</button>
                </div>` : ''}
                <textarea class="ct-textarea" data-field="caption_html">${esc(t.caption_html)}</textarea>

                ${isPromo ? `
                <label class="ct-label" style="margin-top:0.6rem;">รูปภาพ</label>
                <div style="display:flex;gap:0.5rem;">
                    <input type="text" class="ct-input" data-field="image_path" value="${esc(t.image_path)}" placeholder="/app/assets/..." style="flex:1;">
                    <button type="button" class="btn btn-sm btn-outline" onclick="ctUploadImage(this)" title="อัพโหลดรูปใหม่">📷 อัพโหลด</button>
                </div>
                ${t.image_path ? `<div style="margin-top:0.4rem;font-size:0.7rem;color:var(--text-muted);">รูปปัจจุบัน: <code>${esc(t.image_path)}</code></div>` : ''}

                <label class="ct-label" style="margin-top:0.8rem;">🔘 ปุ่มใต้โพสต์ Telegram</label>
                <div class="ct-buttons-list" data-tpl-buttons="${t.id}">
                    ${(t.buttons || []).map((b, i) => ctRenderBtnRow(b, i)).join('')}
                </div>
                <button type="button" class="btn btn-sm btn-outline" onclick="ctAddButton(this)" style="margin-top:0.3rem;">+ เพิ่มปุ่ม</button>
                ` : ''}

                <div class="ct-actions">
                    <button class="btn btn-sm btn-outline" onclick="resetContentTemplate(${t.id})">↩️ ยกเลิก</button>
                    <button class="btn btn-sm btn-primary" onclick="saveContentTemplate(${t.id})">💾 บันทึก</button>
                    <button class="btn btn-sm" onclick="ctDeleteTemplate(${t.id}, '${esc(t.template_key)}')" style="background:#7f1d1d;color:#fff;border:none;">🗑 ลบ</button>
                </div>
              </div>
            `;
        });
        html += '</div>';

        content.innerHTML = html;
        // Cache originals for reset
        window._ctOriginals = {};
        items.forEach(t => { window._ctOriginals[t.id] = { caption: t.caption_html, image: t.image_path }; });
    } catch (e) {
        content.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    }
}

async function saveContentTemplate(id) {
    const card = document.querySelector(`.ct-card[data-tpl="${id}"]`);
    if (!card) return;
    const cap = card.querySelector('[data-field="caption_html"]').value;
    const imgEl = card.querySelector('[data-field="image_path"]');
    const body = { caption_html: cap };
    if (imgEl) body.image_path = imgEl.value;
    const btns = ctCollectButtons(card);
    body.buttons = btns;
    try {
        await api(`/admin/content-templates/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(body),
        });
        toast('💾 บันทึกสำเร็จ — บอตจะใช้รอบถัดไป', 'success');
        if (window._ctOriginals) window._ctOriginals[id] = { caption: cap, image: imgEl ? imgEl.value : '' };
    } catch (e) { toast(e.message, 'error'); }
}

function resetContentTemplate(id) {
    const card = document.querySelector(`.ct-card[data-tpl="${id}"]`);
    if (!card || !window._ctOriginals || !window._ctOriginals[id]) return;
    card.querySelector('[data-field="caption_html"]').value = window._ctOriginals[id].caption;
    const imgEl = card.querySelector('[data-field="image_path"]');
    if (imgEl) imgEl.value = window._ctOriginals[id].image;
    toast('↩️ ยกเลิกการแก้ — กลับเป็นค่าเดิม', 'info');
}


function ctRenderBtnRow(btn, idx) {
    const safeLabel = (btn && btn.label || '').replace(/"/g, '&quot;');
    const safeUrl = (btn && btn.url || '').replace(/"/g, '&quot;');
    return `<div class="ct-btn-row" style="display:flex;gap:0.4rem;align-items:center;margin-bottom:0.4rem;flex-wrap:wrap;">
        <input type="text" class="ct-input" data-btn-field="label" placeholder="ข้อความปุ่ม" value="${safeLabel}" style="flex:1;min-width:140px;">
        <span style="color:var(--text-muted);">→</span>
        <input type="text" class="ct-input" data-btn-field="url" placeholder="https://t.me/... หรือกด '🎁 เลือกโปร'" value="${safeUrl}" style="flex:2;min-width:200px;">
        <button type="button" class="btn btn-sm" onclick="ctPickPromoLink(this)" style="background:#a16207;color:#fff;border:none;border-radius:4px;padding:0.3rem 0.55rem;font-size:0.78rem;white-space:nowrap;">🎁 เลือกโปร</button>
        <button type="button" class="btn btn-sm btn-danger" onclick="ctDeleteButton(this)" title="ลบปุ่มนี้">×</button>
    </div>`;
}

function ctAddButton(btn) {
    const list = btn.previousElementSibling;
    if (!list || !list.classList.contains('ct-buttons-list')) return;
    list.insertAdjacentHTML('beforeend', ctRenderBtnRow({label:'', url:''}, list.children.length));
    list.lastElementChild.querySelector('input').focus();
}



// DAY 0 (2026-06-28): Pick a promotion to auto-generate sales_bot deep link
let _ctPromoPickerCache = null;
async function ctPickPromoLink(btnEl) {
    // Get URL input next to the picker button
    const row = btnEl.closest('.ct-btn-row');
    const urlInput = row?.querySelector('input[data-btn-field="url"]');
    if (!urlInput) return;
    
    // Cache promo list
    if (!_ctPromoPickerCache) {
        try {
            _ctPromoPickerCache = await api('/admin/day0-promos');
        } catch (e) {
            alert('โหลดโปรไม่สำเร็จ');
            return;
        }
    }
    const promos = _ctPromoPickerCache.filter(p => p.is_active);
    
    if (promos.length === 0) {
        alert('ยังไม่มีโปรที่เปิดใช้งาน — สร้างที่หน้า 🎁 จัดการโปร ก่อน');
        return;
    }
    
    // Show selection modal
    const modal = document.createElement('div');
    modal.id = 'ctPromoPickerModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:99999;display:flex;align-items:center;justify-content:center;padding:1rem;';
    
    const items = promos.map(p => {
        let discountLabel = '';
        if (p.discount_type === 'percent') discountLabel = `ลด ${p.discount_value}%`;
        else if (p.discount_type === 'fixed_off') discountLabel = `ลด ฿${p.discount_value}`;
        else if (p.discount_type === 'fixed_price') discountLabel = `฿${p.discount_value}`;
        return `<div onclick="ctApplyPromoLink('${esc(p.code)}'); document.getElementById('ctPromoPickerModal').remove();" 
            style="padding:0.7rem 0.9rem;background:#27272a;border:1px solid #3f3f46;border-radius:6px;cursor:pointer;margin-bottom:0.4rem;"
            onmouseover="this.style.background='#3f3f46'" onmouseout="this.style.background='#27272a'">
            <div style="font-weight:600;font-size:0.9rem;">${esc(p.name)}</div>
            <div style="font-size:0.7rem;color:var(--text-muted);margin-top:0.2rem;font-family:var(--font-mono);">${esc(p.code)} · ${discountLabel}</div>
        </div>`;
    }).join('');
    
    modal.innerHTML = `<div style="background:#1c1c1f;border:1px solid #3f3f46;border-radius:12px;padding:1.2rem;max-width:480px;width:100%;max-height:80vh;overflow-y:auto;">
        <h3 style="margin:0 0 0.6rem;font-size:1rem;">🎁 เลือกโปรโมชั่นสำหรับปุ่มนี้</h3>
        <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:0.8rem;">เลือกแล้วระบบจะใส่ลิงก์เปิด sales bot ให้อัตโนมัติ</div>
        ${items}
        <button class="btn btn-sm" onclick="document.getElementById('ctPromoPickerModal').remove()" style="margin-top:0.5rem;background:#3f3f46;color:#fff;width:100%;">ยกเลิก</button>
    </div>`;
    
    // Store ref to URL input for the apply function
    window._ctPickerTargetInput = urlInput;
    document.body.appendChild(modal);
}

function ctApplyPromoLink(promoCode) {
    const inp = window._ctPickerTargetInput;
    if (!inp) return;
    const SALES_BOT = 'NamwarnJarern_bot';
    inp.value = `https://t.me/${SALES_BOT}?start=${promoCode}`;
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    window._ctPickerTargetInput = null;
}

function ctDeleteButton(btn) {
    btn.closest('.ct-btn-row').remove();
}

function ctCollectButtons(card) {
    const rows = card.querySelectorAll('.ct-btn-row');
    const out = [];
    rows.forEach(r => {
        const lab = r.querySelector('[data-btn-field="label"]').value.trim();
        const url = r.querySelector('[data-btn-field="url"]').value.trim();
        if (lab && url) out.push({label: lab, url: url});
    });
    return out;
}



// ──────────────────────────────────────────────────────────────────
// B.1.D (2026-06-27): Add Template + Add Schedule modals
// ──────────────────────────────────────────────────────────────────


function ctAutoGenKey() {
    // Auto-generate template_key: try transliterate display name, fallback to timestamp
    const nameInput = document.getElementById('ct-add-name');
    const keyInput = document.getElementById('ct-add-key');
    if (!keyInput) return;
    
    const name = (nameInput?.value || '').trim().toLowerCase();
    // Map Thai categories to English prefixes
    const map = {
        'โปร': 'promo_',
        'ลด': 'promo_',
        'sale': 'promo_',
        'sale_': 'promo_',
        'โพสต์': 'post_',
        'โฆษณา': 'ad_',
        'ขาย': 'ad_',
    };
    let prefix = 'post_';
    for (const [k, v] of Object.entries(map)) {
        if (name.includes(k)) { prefix = v; break; }
    }
    // Use timestamp suffix for uniqueness
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const ts = `${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`;
    keyInput.value = prefix + ts;
    keyInput.focus();
}


function ctOpenAddTemplate() {
    const modal = document.createElement('div');
    modal.id = 'addTplModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;display:flex;align-items:center;justify-content:center;padding:1rem;';
    modal.innerHTML = `
        <div style="background:#1c1c1f;border:1px solid #3f3f46;border-radius:12px;padding:1.5rem;max-width:520px;width:100%;max-height:90vh;overflow:auto;">
            <h3 style="margin:0 0 1rem;font-size:1.1rem;">➕ เพิ่มคอนเทนต์ใหม่</h3>
            <div style="margin-bottom:0.75rem;">
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">ชื่อแสดง *</label>
                <input id="ct-add-name" class="ct-input" placeholder="เช่น โปร VIP ฤดูร้อน">
            </div>
            <div style="margin-bottom:0.75rem;">
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">รหัส template_key * (ภาษาอังกฤษ + _)</label>
                <div style="display:flex;gap:0.4rem;">
                    <input id="ct-add-key" class="ct-input" placeholder="เช่น post_2026_06_28_1834" oninput="this.value=this.value.toLowerCase().replace(/[^a-z0-9_]+/g,'_')" style="flex:1;">
                    <button type="button" class="btn btn-sm" onclick="ctAutoGenKey()" style="background:#7c3aed;color:#fff;border:none;border-radius:6px;padding:0.4rem 0.7rem;font-size:0.78rem;white-space:nowrap;">🪄 อัตโนมัติ</button>
                </div>
                <div style="font-size:0.68rem;color:var(--text-muted);margin-top:0.2rem;">ลูกค้าไม่เห็นรหัสนี้ — ใช้ภายในระบบเท่านั้น</div>
            </div>
            <div style="margin-bottom:0.75rem;">
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">หมวด</label>
                <select id="ct-add-cat" class="ct-input">
                    <option value="promo">🎁 โปรโมชั่น</option>
                    <option value="teaser_style">📸 สไตล์ตัวอย่าง</option>
                </select>
            </div>
            <div style="margin-bottom:0.75rem;">
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">ข้อความ caption (HTML)</label>
                <textarea id="ct-add-cap" class="ct-textarea" placeholder="🌟 <b>โปรพิเศษ</b> 🌟\n\n..."></textarea>
            </div>
            <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem;">
                <button class="btn btn-secondary" onclick="document.getElementById('addTplModal').remove()">ยกเลิก</button>
                <button class="btn btn-primary" onclick="ctSubmitAddTemplate()">✅ สร้าง</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    setTimeout(() => document.getElementById('ct-add-name')?.focus(), 100);
}

async function ctSubmitAddTemplate() {
    const name = document.getElementById('ct-add-name').value.trim();
    const key = document.getElementById('ct-add-key').value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_');
    const cat = document.getElementById('ct-add-cat').value;
    const cap = document.getElementById('ct-add-cap').value;
    if (!name || !key) { alert('กรอกชื่อแสดง + รหัส template_key'); return; }
    try {
        await api('/admin/content-templates', { method: 'POST', body: JSON.stringify({ template_key: key, display_name: name, category: cat, caption_html: cap }) });
        document.getElementById('addTplModal').remove();
        toast('✅ สร้าง template แล้ว');
        renderContentEditor();
    } catch (e) {
        alert('❌ ' + (e.message || 'สร้างไม่สำเร็จ'));
    }
}

async function ctDeleteTemplate(id, key) {
    if (!await confirmModal({ message: `ลบ template "${key}" และ schedule ที่ใช้ template นี้ ?`, dangerous: true })) return;
    try {
        await api(`/admin/content-templates/${id}`, { method: 'DELETE' });
        toast('🗑 ลบ template + schedule ที่เกี่ยวข้องแล้ว');
        renderContentEditor();
    } catch (e) {
        alert('❌ ' + (e.message || 'ลบไม่สำเร็จ'));
    }
}

async function schedOpenAdd() {
    // Load promo templates as options
    let templates = [];
    try {
        templates = (await api('/admin/content-templates?category=promo')) || [];
    } catch (e) { templates = []; }
    const optsHtml = templates.map(t => `<option value="${esc(t.template_key)}">${esc(t.display_name)} (${esc(t.template_key)})</option>`).join('') ||
                     '<option value="">— ยังไม่มี template — สร้างที่หน้า Content Editor ก่อน —</option>';
    
    const modal = document.createElement('div');
    modal.id = 'addSchedModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:99999;display:flex;align-items:center;justify-content:center;padding:1rem;';
    modal.innerHTML = `
        <div style="background:#1c1c1f;border:1px solid #3f3f46;border-radius:12px;padding:1.5rem;max-width:480px;width:100%;">
            <h3 style="margin:0 0 1rem;font-size:1.1rem;">➕ เพิ่มตารางเวลา ใหม่</h3>
            <div style="margin-bottom:0.75rem;">
                <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">Template ที่จะโพสต์ *</label>
                <select id="sched-add-tpl" class="ct-input">${optsHtml}</select>
                <div style="font-size:0.7rem;color:var(--text-muted);margin-top:0.3rem;">เลือก template ที่สร้างไว้ในหน้า Content Editor</div>
            </div>
            <div style="margin-bottom:0.75rem;display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;">
                <div>
                    <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">ชั่วโมง (0-23)</label>
                    <input id="sched-add-h" class="ct-input" type="number" min="0" max="23" value="9">
                </div>
                <div>
                    <label style="display:block;font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem;text-transform:uppercase;">นาที (0-59)</label>
                    <input id="sched-add-m" class="ct-input" type="number" min="0" max="59" value="0">
                </div>
            </div>
            <div style="background:#2a2a2e;border:1px solid #3f3f46;border-radius:6px;padding:0.6rem 0.75rem;font-size:0.75rem;color:var(--text-muted);margin:1rem 0;">
                ℹ️ หลังกดสร้าง บอตจะ <b>restart 1 ครั้ง</b> เพื่อให้ schedule ใหม่มีผล (รวดเร็ว 5-10 วินาที)
            </div>
            <div style="display:flex;gap:0.5rem;justify-content:flex-end;">
                <button class="btn btn-secondary" onclick="document.getElementById('addSchedModal').remove()">ยกเลิก</button>
                <button class="btn btn-primary" onclick="schedSubmitAdd()">✅ สร้าง + restart</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

async function schedSubmitAdd() {
    const tpl = document.getElementById('sched-add-tpl').value;
    const h = parseInt(document.getElementById('sched-add-h').value);
    const m = parseInt(document.getElementById('sched-add-m').value);
    if (!tpl) { alert('เลือก template ก่อน'); return; }
    if (isNaN(h) || h<0 || h>23) { alert('ชั่วโมง 0-23'); return; }
    if (isNaN(m) || m<0 || m>59) { alert('นาที 0-59'); return; }
    try {
        await api(`/admin/bots/${encodeURIComponent(_schedBot||'content_bot')}/schedules`, {
            method: 'POST',
            body: JSON.stringify({ template_key: tpl, schedule_hour: h, schedule_minute: m })
        });
        document.getElementById('addSchedModal').remove();
        toast('✅ สร้าง schedule + กำลัง restart บอต...');
        // Restart the bot so new schedule binds
        try {
            await api(`/admin/bots/charoenpon-${(_schedBot||'content_bot').replace('_','-')}/restart`, { method: 'POST' });
        } catch (e) { /* non-fatal */ }
        setTimeout(() => { toast('✅ Schedule พร้อมแล้ว'); renderBotSchedules(); }, 4000);
    } catch (e) {
        alert('❌ ' + (e.message || 'สร้างไม่สำเร็จ'));
    }
}

async function schedDelete(id, jobName) {
    if (!await confirmModal({ message: `ลบ schedule "${jobName}" ?\n\nบอตจะ restart 1 ครั้งเพื่อให้มีผล`, dangerous: true })) return;
    try {
        await api(`/admin/bots/schedules/${id}`, { method: 'DELETE' });
        toast('🗑 ลบแล้ว + กำลัง restart...');
        try { await api(`/admin/bots/charoenpon-${(_schedBot||'content_bot').replace('_','-')}/restart`, { method: 'POST' }); } catch (e) {}
        setTimeout(() => renderBotSchedules(), 4000);
    } catch (e) {
        alert('❌ ' + (e.message || 'ลบไม่สำเร็จ'));
    }
}


// =============================================================
// DAY 0 (2026-06-28): Promo Manager UI (Day-0 unified promotions)
// Renders inside #promo-area when promoTab === 'campaigns_new'
// =============================================================

let _dayZeroPromos = [];
let _dayZeroPackages = [];
let _dayZeroGroups = [];
let _dayZeroEditId = null;

async function loadDayZeroPromos() {
    const area = document.getElementById('promo-area');
    if (!area) return;
    area.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
        const [promos, packages, groups] = await Promise.all([
            api('/admin/day0-promos'),
            api('/admin/day0-promos/packages'),
            api('/admin/day0-promos/groups'),
        ]);
        _dayZeroPromos = promos || [];
        _dayZeroPackages = packages || [];
        _dayZeroGroups = groups || [];
        renderDayZeroPromoList();
    } catch (e) {
        area.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><p>โหลดรายการโปรไม่ได้: ${esc(e.message || e)}</p></div>`;
    }
}

function renderDayZeroPromoList() {
    const area = document.getElementById('promo-area');
    if (!area) return;

    const stats = `
        <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;margin-bottom:0.85rem;">
            <div>
                <h2 style="margin:0;font-size:1.15rem;">🎁 จัดการโปรโมชั่น (ส่วนลด / แคมเปญพิเศษเท่านั้น)</h2>
                <div style="font-size:0.72rem;color:var(--text-muted);">เฉพาะโปรที่มีส่วนลด/แคมเปญพิเศษ · โพสต์โฆษณาสินค้าปกติอยู่ที่ <b>📝 คอนเทนต์บอท</b></div>
            </div>
            <button class="btn btn-primary" onclick="openDayZeroPromoForm(null)" style="white-space:nowrap;margin-left:auto;">➕ เพิ่มโปรใหม่</button>
        </div>
    `;

    if (_dayZeroPromos.length === 0) {
        area.innerHTML = stats + '<div class="empty-state"><div class="icon">🎁</div><p>ยังไม่มีโปร — กดปุ่ม "เพิ่มโปรใหม่" เพื่อเริ่ม</p></div>';
        return;
    }

    const rows = _dayZeroPromos.map(p => {
        const pkgs = Array.isArray(p.package_codes) ? p.package_codes : [];
        const pkgLabels = pkgs.map(c => {
            const pkg = _dayZeroPackages.find(x => x.tier === c);
            return pkg ? pkg.name : c;
        }).slice(0, 3).join(', ') + (pkgs.length > 3 ? ` +${pkgs.length - 3}` : '');

        let discountLabel = 'ราคาเต็ม';
        if (p.discount_type === 'percent') discountLabel = `ลด ${p.discount_value}%`;
        else if (p.discount_type === 'fixed_off') discountLabel = `ลด ฿${p.discount_value}`;
        else if (p.discount_type === 'fixed_price') discountLabel = `ราคา ฿${p.discount_value}`;

        const times = Array.isArray(p.post_times) ? p.post_times : [];
        const timeLabels = times.map(t => `${String(t.hour||0).padStart(2,'0')}:${String(t.minute||0).padStart(2,'0')}`).join(', ') || '— ไม่ตั้งเวลา —';

        const _fmtBkk = (iso) => {
            if (!iso) return null;
            const d = new Date(iso);
            return d.toLocaleString('th-TH', {timeZone: 'Asia/Bangkok', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
        };
        const dateRange = (p.starts_at || p.ends_at)
            ? `${_fmtBkk(p.starts_at) || 'ตอนนี้'} → ${_fmtBkk(p.ends_at) || 'ตลอดไป'}`
            : 'ตลอดไป';

        const onCls = p.is_active ? 'active' : '';
        const onLabel = p.is_active ? '🟢 เปิด' : '⚪ ปิด';

        return `
        <div class="dz-promo-card" data-id="${p.id}" style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.9rem 1rem;margin-bottom:0.6rem;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem;">
                <div style="flex:1;">
                    <div style="font-weight:600;font-size:1rem;">${esc(p.name)}</div>
                    <div style="font-size:0.7rem;color:var(--text-muted);font-family:var(--font-mono,monospace);margin-top:0.15rem;">${esc(p.code)}</div>
                </div>
                <div style="display:flex;gap:0.4rem;flex-shrink:0;align-items:center;">
                    <button class="btn btn-sm ${onCls}" onclick="toggleDayZeroPromo(${p.id}, ${!p.is_active})" style="font-size:0.7rem;padding:0.3rem 0.55rem;background:${p.is_active?'#10b981':'#3f3f46'};color:#fff;border:none;border-radius:6px;cursor:pointer;">${onLabel}</button>
                    <button class="btn btn-sm" onclick="openDayZeroPromoForm(${p.id})" style="font-size:0.7rem;padding:0.3rem 0.55rem;background:#3f3f46;color:#fff;border:none;border-radius:6px;cursor:pointer;">✏️ แก้ไข</button>
                    <button class="btn btn-sm" onclick="deleteDayZeroPromo(${p.id}, '${esc(p.code)}')" style="font-size:0.7rem;padding:0.3rem 0.55rem;background:#7f1d1d;color:#fff;border:none;border-radius:6px;cursor:pointer;">🗑</button>
                </div>
            </div>
            <div style="margin-top:0.55rem;display:grid;grid-template-columns:auto 1fr;gap:0.35rem 0.65rem;font-size:0.78rem;color:var(--text-muted);">
                <div>📦 แพ็คเกจ:</div><div style="color:var(--text);">${esc(pkgLabels) || '— ยังไม่ได้เลือก —'}</div>
                <div>💰 ราคา:</div><div style="color:var(--text);">${esc(discountLabel)} · ใช้ได้ ${p.valid_hours||48} ชม.</div>
                <div>📅 ระยะ:</div><div style="color:var(--text);">${esc(dateRange)}</div>
            </div>
        </div>`;
    }).join('');

    area.innerHTML = stats + '<div id="dz-promo-list">' + rows + '</div>';
}


// ──────────────────────────────────────────────────────────────
// CREATE/EDIT MODAL
// ──────────────────────────────────────────────────────────────
function openDayZeroPromoForm(promoId) {
    _dayZeroEditId = promoId;
    const existing = promoId ? _dayZeroPromos.find(p => p.id === promoId) : null;
    const data = existing || {
        code: '',
        name: '',
        is_active: false,
        package_codes: [],
        discount_type: 'percent',
        discount_value: 0,
        valid_hours: 48,
        starts_at: null,
        ends_at: null,
    };

    const title = existing ? `✏️ แก้ไขโปร — ${esc(existing.name)}` : '➕ เพิ่มโปรโมชั่นใหม่';
    const codeReadonly = existing ? 'readonly' : '';

    // Multi-select packages
    const pkgCheckboxes = _dayZeroPackages.map(pkg => {
        const checked = (data.package_codes || []).includes(pkg.tier) ? 'checked' : '';
        return `<label style="display:flex;align-items:center;gap:0.4rem;padding:0.35rem 0.6rem;background:#1c1c1f;border:1px solid #3f3f46;border-radius:6px;cursor:pointer;font-size:0.8rem;margin-bottom:0.3rem;">
            <input type="checkbox" name="dz-pkg" value="${esc(pkg.tier)}" ${checked} style="margin:0;">
            <span style="flex:1;">${esc(pkg.name)}</span>
            <span style="color:var(--text-muted);font-size:0.7rem;font-family:var(--font-mono);">${esc(pkg.tier)} · ฿${pkg.price}</span>
        </label>`;
    }).join('');

    // Convert UTC date to Bangkok local time for the datetime-local input
    const _toBkkLocal = (iso) => {
        if (!iso) return '';
        const d = new Date(iso);
        const bkk = new Date(d.getTime() + (7 * 60 - d.getTimezoneOffset()) * 60000);
        return bkk.toISOString().slice(0, 16);
    };
    const startsStr = _toBkkLocal(data.starts_at);
    const endsStr = _toBkkLocal(data.ends_at);

    const modal = document.createElement('div');
    modal.id = 'dzPromoModal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:99999;display:flex;align-items:flex-start;justify-content:center;padding:1.5rem;overflow-y:auto;';
    modal.innerHTML = `
        <div style="background:#1c1c1f;border:1px solid #3f3f46;border-radius:12px;padding:1.5rem;max-width:580px;width:100%;margin:auto;">
            <h3 style="margin:0 0 0.5rem;font-size:1.15rem;">${title}</h3>
            <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:1rem;">
                ⚠️ <b>โปรโมชั่นแค่ตั้งส่วนลด</b> — โพสต์โฆษณาไปทำที่ <b>📝 คอนเทนต์บอท</b>
            </div>

            <!-- BASIC -->
            <fieldset style="border:1px solid #3f3f46;border-radius:8px;padding:0.7rem 0.9rem;margin-bottom:0.85rem;">
                <legend style="padding:0 0.4rem;font-size:0.75rem;color:var(--text-muted);">📝 ชื่อ + รหัส</legend>
                <div style="margin-bottom:0.6rem;">
                    <label class="ct-label">ชื่อโปร *</label>
                    <input id="dz-name" class="ct-input" value="${esc(data.name)}" placeholder="เช่น ลด 50% สิ้นเดือน">
                </div>
                <div style="margin-bottom:0.6rem;">
                    <label class="ct-label">รหัส (a-z, 0-9, _) *</label>
                    <input id="dz-code" class="ct-input" value="${esc(data.code)}" placeholder="เช่น end_50" ${codeReadonly}
                        oninput="this.value=this.value.toLowerCase().replace(/[^a-z0-9_]+/g,'_')">
                </div>
                <label style="display:flex;align-items:center;gap:0.5rem;font-size:0.85rem;">
                    <input type="checkbox" id="dz-active" ${data.is_active ? 'checked' : ''}>
                    <span>✅ เปิดใช้งานโปรนี้ (ราคา sales bot จะลดทันที)</span>
                </label>
            </fieldset>

            <!-- PRICING -->
            <fieldset style="border:1px solid #3f3f46;border-radius:8px;padding:0.7rem 0.9rem;margin-bottom:0.85rem;">
                <legend style="padding:0 0.4rem;font-size:0.75rem;color:var(--text-muted);">💰 ส่วนลด</legend>
                <label class="ct-label">ใช้กับแพ็คเกจ (เลือกได้หลาย):</label>
                <div style="max-height:200px;overflow-y:auto;padding:0.3rem;background:#0a0a0a;border:1px solid #27272a;border-radius:6px;">
                    ${pkgCheckboxes}
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.6rem;margin-top:0.7rem;">
                    <div>
                        <label class="ct-label">ประเภทส่วนลด</label>
                        <select id="dz-discount-type" class="ct-input">
                            <option value="percent" ${data.discount_type==='percent'?'selected':''}>ลด %</option>
                            <option value="fixed_off" ${data.discount_type==='fixed_off'?'selected':''}>ลด ฿ ตายตัว</option>
                            <option value="fixed_price" ${data.discount_type==='fixed_price'?'selected':''}>ราคาตายตัว</option>
                        </select>
                    </div>
                    <div>
                        <label class="ct-label">จำนวน</label>
                        <input id="dz-discount-value" type="number" min="0" class="ct-input" value="${data.discount_value || 0}">
                    </div>
                </div>
                <div style="margin-top:0.7rem;">
                    <label class="ct-label">ใช้ได้ภายใน (ชั่วโมง หลังลูกค้ากด)</label>
                    <input id="dz-valid-hours" type="number" min="1" max="240" class="ct-input" value="${data.valid_hours || 48}">
                </div>
            </fieldset>

            <!-- DATE RANGE -->
            <fieldset style="border:1px solid #3f3f46;border-radius:8px;padding:0.7rem 0.9rem;margin-bottom:1rem;">
                <legend style="padding:0 0.4rem;font-size:0.75rem;color:var(--text-muted);">📅 ระยะเวลาโปร (ไม่บังคับ)</legend>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.6rem;">
                    <div>
                        <label class="ct-label">เริ่ม</label>
                        <input id="dz-starts" type="datetime-local" class="ct-input" value="${startsStr}">
                    </div>
                    <div>
                        <label class="ct-label">จบ</label>
                        <input id="dz-ends" type="datetime-local" class="ct-input" value="${endsStr}">
                    </div>
                </div>
                <div style="font-size:0.7rem;color:var(--text-muted);margin-top:0.3rem;">ปล่อยว่าง = ใช้ได้ตลอด</div>
            </fieldset>

            <div style="display:flex;gap:0.5rem;justify-content:flex-end;">
                <button class="btn" onclick="document.getElementById('dzPromoModal').remove()" style="background:#3f3f46;color:#fff;">ยกเลิก</button>
                <button class="btn btn-primary" onclick="submitDayZeroPromo()">💾 บันทึก</button>
            </div>
        </div>
        <style>
            #dzPromoModal .ct-input { background:#27272a;color:#fff;border:1px solid #3f3f46;border-radius:6px;padding:0.4rem 0.55rem;font-size:0.85rem;width:100%; }
            #dzPromoModal .ct-label { display:block;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.25rem; }
        </style>
    `;
    document.body.appendChild(modal);
    setTimeout(() => document.getElementById('dz-name')?.focus(), 100);
}

function dzAddTime() {
    const area = document.getElementById('dz-times-area');
    if (!area) return;
    const idx = area.querySelectorAll('.dz-time-row').length;
    const row = document.createElement('div');
    row.className = 'dz-time-row';
    row.style.cssText = 'display:flex;gap:0.3rem;align-items:center;margin-bottom:0.3rem;';
    row.innerHTML = `
        <input type="number" min="0" max="23" value="9" data-idx="${idx}" data-fld="hour" class="dz-time-input" style="width:55px;text-align:center;">
        <span>:</span>
        <input type="number" min="0" max="59" value="0" data-idx="${idx}" data-fld="minute" class="dz-time-input" style="width:55px;text-align:center;">
        <button class="btn btn-sm" onclick="this.parentElement.remove()" style="font-size:0.75rem;padding:0.2rem 0.5rem;background:#7f1d1d;color:#fff;border:none;border-radius:4px;">×</button>
    `;
    area.appendChild(row);
}

function dzRemoveTime(idx) {
    const rows = document.querySelectorAll('#dz-times-area .dz-time-row');
    if (rows[idx]) rows[idx].remove();
}

async function submitDayZeroPromo() {
    const name = document.getElementById('dz-name').value.trim();
    const code = document.getElementById('dz-code').value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_');
    if (!name || !code) { alert('กรอกชื่อโปร + รหัส'); return; }

    const pkgCodes = Array.from(document.querySelectorAll('input[name="dz-pkg"]:checked')).map(x => x.value);
    if (!pkgCodes.length) { alert('เลือกแพ็คเกจอย่างน้อย 1 อัน'); return; }

    const discountType = document.getElementById('dz-discount-type').value;
    const discountValue = parseFloat(document.getElementById('dz-discount-value').value) || 0;
    const validHours = parseInt(document.getElementById('dz-valid-hours').value) || 48;
    const isActive = document.getElementById('dz-active').checked;
    // Treat datetime-local input as Bangkok time (UTC+7), send with explicit offset
    const _appendBkkTz = (s) => s ? (s.length === 16 ? s + ':00+07:00' : s + '+07:00') : null;
    const startsStr = _appendBkkTz(document.getElementById('dz-starts').value);
    const endsStr = _appendBkkTz(document.getElementById('dz-ends').value);

    const payload = {
        code: code,
        name: name,
        is_active: isActive,
        package_codes: pkgCodes,
        discount_type: discountType,
        discount_value: discountValue,
        valid_hours: validHours,
        starts_at: startsStr || null,
        ends_at: endsStr || null,
    };

    try {
        if (_dayZeroEditId) {
            await api(`/admin/day0-promos/${_dayZeroEditId}`, { method: 'PATCH', body: JSON.stringify(payload) });
            toast('✅ บันทึกแล้ว');
        } else {
            await api('/admin/day0-promos', { method: 'POST', body: JSON.stringify(payload) });
            toast('✅ สร้างโปรใหม่แล้ว');
        }
        document.getElementById('dzPromoModal')?.remove();
        await loadDayZeroPromos();
    } catch (e) {
        alert('❌ ' + (e.message || 'บันทึกไม่สำเร็จ'));
    }
}

async function toggleDayZeroPromo(id, newState) {
    try {
        await api(`/admin/day0-promos/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: newState }) });
        toast(newState ? '🟢 เปิดโปรแล้ว' : '⚪ ปิดโปรแล้ว');
        await loadDayZeroPromos();
    } catch (e) {
        alert('❌ ' + (e.message || 'เปลี่ยนสถานะไม่สำเร็จ'));
    }
}

async function deleteDayZeroPromo(id, code) {
    if (!await confirmModal({ message: `ลบโปร "${code}" ?\n\nการลบนี้จะลบข้อมูลทุก click ของลูกค้าที่กดโปรนี้ด้วย`, dangerous: true })) return;
    try {
        await api(`/admin/day0-promos/${id}`, { method: 'DELETE' });
        toast('🗑 ลบแล้ว');
        await loadDayZeroPromos();
    } catch (e) {
        alert('❌ ' + (e.message || 'ลบไม่สำเร็จ'));
    }
}


// ============================================================
//  📨 Customer Journey (DM อัตโนมัติ) — added 2026-06-28
// ============================================================
let _journeyCache = null;
let _journeyTab = 'welcome';

async function renderJourney() {
    const content = document.getElementById('page-content');
    content.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    try {
        _journeyCache = await api('/admin/journey-templates');
    } catch (e) {
        content.innerHTML = `<div class="err">โหลดไม่สำเร็จ: ${e.message}</div>`;
        return;
    }
    let html = `
        <div class="tabs" style="margin-bottom:1rem">
            <div class="tab ${_journeyTab==='welcome'?'active':''}" onclick="_journeyTab='welcome';renderJourney()">👋 Welcome (ลูกค้าใหม่ 24 ชม.)</div>
            <div class="tab ${_journeyTab==='comeback'?'active':''}" onclick="_journeyTab='comeback';renderJourney()">💌 Comeback (ลูกค้าหาย)</div>
            <div class="tab ${_journeyTab==='exit'?'active':''}" onclick="_journeyTab='exit';renderJourney()">📊 Exit Survey (หมดอายุ)</div>
        </div>
    `;
    const flowDesc = {
        welcome: '🆕 ลูกค้าใหม่กด /start → ส่ง 4 stages ใน 24 ชม. (Instant / 3h / 12h / 23h) พร้อมส่วนลด 25%',
        comeback: '💔 ลูกค้าหายไป → DM 2 รอบ x 4 variants (A/B test) — เลือก variant อัตโนมัติตาม conversion rate',
        exit: '📤 ลูกค้าหมดอายุไม่ต่อ → ถามเหตุผล + ส่งส่วนลดตาม reason_code (50/40/30/20%)',
    };
    html += `<div style="background:rgba(255,255,255,0.05);padding:0.7rem;border-radius:0.5rem;margin-bottom:1rem;font-size:0.9rem;opacity:0.85">${flowDesc[_journeyTab]||''}</div>`;
    
    const filtered = _journeyCache.filter(t => t.flow === _journeyTab);
    if (filtered.length === 0) {
        html += '<div class="empty-state"><div class="icon">📭</div><p>ไม่มี template</p></div>';
    } else {
        html += '<div class="grid" style="grid-template-columns:1fr;gap:0.8rem">';
        filtered.forEach(t => {
            const wiredBadge = t.wired
                ? '<span style="color:var(--success);font-size:0.75rem">✅ ใช้งานจริง</span>'
                : '<span style="color:#f59e0b;font-size:0.75rem">⏳ ยังไม่ wire (แก้ได้แต่ลูกค้ายังเห็นข้อความเก่า)</span>';
            const preview = (t.content_html || '').replace(/<[^>]+>/g, '').slice(0, 200);
            html += `
                <div class="card" style="padding:1rem">
                    <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:0.5rem">
                        <div>
                            <div style="font-weight:700;font-size:1.05rem">${esc(t.label)}</div>
                            <div style="font-size:0.75rem;opacity:0.6;margin-top:0.2rem">${esc(t.message_key)} · ${wiredBadge}</div>
                        </div>
                        ${hasRole('owner') ? `<button class="btn btn-sm btn-primary" onclick="editJourneyTemplate('${t.message_key}')">✏️ แก้ไข</button>` : ''}
                    </div>
                    <div style="font-size:0.85rem;opacity:0.8;line-height:1.6;white-space:pre-line;max-height:120px;overflow:hidden;padding:0.5rem;background:rgba(0,0,0,0.2);border-radius:0.4rem">${esc(preview)}${preview.length === 200 ? '...' : ''}</div>
                    <div style="font-size:0.75rem;opacity:0.5;margin-top:0.4rem">${t.description || ''}</div>
                </div>`;
        });
        html += '</div>';
    }
    content.innerHTML = html;
}

function editJourneyTemplate(messageKey) {
    const t = (_journeyCache || []).find(x => x.message_key === messageKey);
    if (!t) { toast('ไม่พบ template', 'error'); return; }
    const placeholders = Array.isArray(t.available_placeholders) ? t.available_placeholders : [];
    const placeholderHints = placeholders.map(p => `<code style="background:rgba(255,255,255,0.1);padding:0.1rem 0.4rem;border-radius:0.3rem;font-size:0.85rem">{${p}}</code>`).join(' ');
    openModal('✏️ แก้: ' + t.label, `
        <div class="form-group"><label>คำอธิบาย</label><input id="jrn-desc" value="${esc(t.description || '')}"></div>
        <div class="form-group">
            <label>เนื้อหา (HTML รองรับ &lt;b&gt; &lt;i&gt; &lt;a&gt;)</label>
            <textarea id="jrn-content" rows="14" style="font-family:monospace;font-size:0.85rem;width:100%">${esc(t.content_html || '')}</textarea>
        </div>
        ${placeholders.length ? `<div style="margin:0.5rem 0;font-size:0.85rem;opacity:0.85">📝 Placeholders ที่ใช้ได้: ${placeholderHints}</div>` : ''}
        <div style="display:flex;gap:0.5rem;margin-top:1rem">
            <button class="btn btn-primary" style="flex:1" onclick="saveJourneyTemplate('${messageKey}')">💾 บันทึก</button>
            <button class="btn btn-outline" onclick="closeModal()">ยกเลิก</button>
        </div>
    `, { wide: true });
}

async function saveJourneyTemplate(messageKey) {
    const content_html = document.getElementById('jrn-content').value;
    const description = document.getElementById('jrn-desc').value;
    if (!content_html.trim()) { toast('เนื้อหาว่างไม่ได้', 'error'); return; }
    try {
        await api(`/admin/journey-templates/${messageKey}`, {
            method: 'PATCH',
            body: JSON.stringify({ content_html, description }),
        });
        toast('บันทึกแล้ว ✅ (cache จะรีเฟรชใน 60 วินาที)', 'success');
        closeModal();
        renderJourney();
    } catch (e) { toast(e.message, 'error'); }
}


// ============================================================
//  🚦 System Health Dashboard — added 2026-06-28
// ============================================================
let _healthTimer = null;

async function renderSystemHealth() {
    const content = document.getElementById('page-content');
    if (_healthTimer) clearInterval(_healthTimer);

    async function loadAndRender() {
        let data;
        try {
            data = await api('/admin/health/overview');
        } catch (e) {
            content.innerHTML = `<div class="err">โหลดไม่สำเร็จ: ${e.message}</div>`;
            return;
        }
        const colorMap = { ok: '#10b981', warn: '#f59e0b', critical: '#ef4444', unknown: '#6b7280' };
        const labelMap = { ok: '✅ ปกติ', warn: '⚠️ เฝ้าระวัง', critical: '🚨 วิกฤต', unknown: '❓ ไม่รู้' };
        const overallColor = colorMap[data.overall_health] || '#6b7280';
        const overallLabel = labelMap[data.overall_health] || '?';
        
        let html = `
            <div style="display:flex;justify-content:space-between;align-items:center;background:${overallColor}22;border-left:6px solid ${overallColor};padding:1rem;border-radius:0.6rem;margin-bottom:1.5rem">
                <div>
                    <div style="font-size:0.8rem;opacity:0.7">สถานะรวม</div>
                    <div style="font-size:1.4rem;font-weight:700;color:${overallColor}">${overallLabel}</div>
                </div>
                <div style="text-align:right;font-size:0.75rem;opacity:0.6">
                    ตรวจล่าสุด<br>${new Date(data.checked_at).toLocaleTimeString('th-TH')}
                </div>
            </div>
        `;

        // Bots section
        html += '<h3 style="margin-bottom:0.7rem">🤖 บอต (Containers)</h3>';
        html += '<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:0.7rem;margin-bottom:1.5rem">';
        (data.bots || []).forEach(b => {
            const c = colorMap[b.health] || '#6b7280';
            const icon = b.is_up ? '✅' : '❌';
            html += `
                <div class="card" style="padding:0.8rem;border-left:4px solid ${c}">
                    <div style="display:flex;justify-content:space-between;align-items:start">
                        <div>
                            <div style="font-weight:600">${icon} ${esc(b.label)}</div>
                            <div style="font-size:0.75rem;opacity:0.6;margin-top:0.2rem">${esc(b.container)}</div>
                        </div>
                        ${b.critical ? '<span style="font-size:0.65rem;color:#ef4444">CRITICAL</span>' : ''}
                    </div>
                    <div style="font-size:0.8rem;opacity:0.8;margin-top:0.4rem">${esc((b.status||'').substring(0, 60))}</div>
                </div>
            `;
        });
        html += '</div>';

        // Payment health
        const p = data.payment || {};
        const pc = colorMap[p.health] || '#6b7280';
        html += '<h3 style="margin-bottom:0.7rem">💳 ระบบจ่ายเงิน</h3>';
        html += `
            <div class="card" style="padding:1rem;border-left:4px solid ${pc};margin-bottom:1.5rem">
                <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem">
                    <div><div style="font-size:0.75rem;opacity:0.6">Pending 24h</div><div style="font-size:1.4rem;font-weight:700">${p.pending_24h ?? '-'}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">Stuck >30min</div><div style="font-size:1.4rem;font-weight:700;color:${(p.stuck_30min || 0) > 5 ? '#ef4444' : 'inherit'}">${p.stuck_30min ?? '-'}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">Issues</div><div style="font-size:1.4rem;font-weight:700">${p.issues_count ?? 0}</div></div>
                </div>
                ${(p.issues || []).length > 0 ? `<div style="margin-top:0.7rem;padding:0.6rem;background:rgba(239,68,68,0.1);border-radius:0.4rem;font-size:0.85rem">${(p.issues || []).map(i => `• ${esc(i)}`).join('<br>')}</div>` : ''}
            </div>
        `;

        // Slip2Go
        const sg = data.slip2go || {};
        const sgc = colorMap[sg.health] || '#6b7280';
        html += '<h3 style="margin-bottom:0.7rem">📱 Slip2Go</h3>';
        html += `
            <div class="card" style="padding:1rem;border-left:4px solid ${sgc};margin-bottom:1.5rem">
                <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem">
                    <div><div style="font-size:0.75rem;opacity:0.6">Failures 24h</div><div style="font-size:1.4rem;font-weight:700;color:${(sg.failures_24h || 0) > 20 ? '#ef4444' : 'inherit'}">${sg.failures_24h ?? '-'}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">Queued</div><div style="font-size:1.4rem;font-weight:700">${sg.queued ?? '-'}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">Last Confirm</div><div style="font-size:0.85rem;font-weight:600">${sg.last_confirm ? new Date(sg.last_confirm).toLocaleString('th-TH') : '—'}</div></div>
                </div>
            </div>
        `;

        // Database
        const db = data.database || {};
        const dbc = colorMap[db.health] || '#6b7280';
        html += '<h3 style="margin-bottom:0.7rem">💾 ฐานข้อมูล</h3>';
        html += `
            <div class="card" style="padding:1rem;border-left:4px solid ${dbc};margin-bottom:1.5rem">
                <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem">
                    <div><div style="font-size:0.75rem;opacity:0.6">Active connections</div><div style="font-size:1.4rem;font-weight:700">${db.active_connections ?? '-'}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">DB size</div><div style="font-size:1.2rem;font-weight:700">${esc(db.db_size || '-')}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">Users total</div><div style="font-size:1.4rem;font-weight:700">${fmt(db.users_total || 0)}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">Active subs</div><div style="font-size:1.4rem;font-weight:700">${fmt(db.active_subs || 0)}</div></div>
                </div>
            </div>
        `;

        // DMs
        const dm = data.dms || {};
        const dmc = colorMap[dm.health] || '#6b7280';
        html += '<h3 style="margin-bottom:0.7rem">📨 DM</h3>';
        html += `
            <div class="card" style="padding:1rem;border-left:4px solid ${dmc};margin-bottom:1rem">
                <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem">
                    <div><div style="font-size:0.75rem;opacity:0.6">Blocked users</div><div style="font-size:1.4rem;font-weight:700">${fmt(dm.blocked_users || 0)}</div></div>
                    <div><div style="font-size:0.75rem;opacity:0.6">DMs sent 24h</div><div style="font-size:1.4rem;font-weight:700">${fmt(dm.dms_sent_24h || 0)}</div></div>
                </div>
            </div>
        `;

        html += '<div style="margin-top:1rem;text-align:center;opacity:0.6;font-size:0.8rem">🔄 รีเฟรชอัตโนมัติ ทุก 30 วินาที</div>';

        content.innerHTML = html;
    }

    await loadAndRender();
    _healthTimer = setInterval(loadAndRender, 30000);
}

// Cleanup timer on navigate
window.addEventListener('beforeunload', () => { if (_healthTimer) clearInterval(_healthTimer); });


// ============================================================
//  ⚙️ System Config (promo_config CRUD) — added 2026-06-28
// ============================================================
const _CONFIG_GROUPS = {
    welcome_journey: { icon: '👋', label: 'Welcome (ลูกค้าใหม่ 24 ชม.)', desc: 'ส่วนลด + เปิด/ปิดระบบ' },
    comeback:        { icon: '💌', label: 'Comeback (ลูกค้าหาย)', desc: 'ส่วนลด R1/R2 + จำนวนวันรอ + DM ต่อวัน' },
    exit_survey:     { icon: '📤', label: 'Exit Survey (หมดอายุ)', desc: 'ส่วนลดตาม tier (50/40/30/20%)' },
    retention:       { icon: '🔄', label: 'Retention (ใกล้หมด)', desc: 'ส่วนลดก่อน-หลังหมดอายุ' },
    quickbuy:        { icon: '⚡', label: 'Quick Buy', desc: 'ส่วนลด + ระยะเวลาใช้ได้' },
    loyalty:         { icon: '🏆', label: 'ระบบยศ Bronze/Silver/Diamond', desc: 'เกณฑ์ขึ้นยศ' },
    cron:            { icon: '⏰', label: 'เวลารัน Cron jobs', desc: 'ปรับเวลา DM อัตโนมัติ (ต้อง restart sales-bot)' },
    gacha_discount:  { icon: '🎰', label: 'Gacha Discount', desc: 'cap ส่วนลดที่กาชาได้รับ' },
    group_bot:       { icon: '🤖', label: 'Group Bot', desc: 'เปิด/ปิด feature ในกลุ่ม' },
    links:           { icon: '🔗', label: 'ลิงก์ภายนอก', desc: 'URL credits/review/etc.' },
};

async function loadSystemConfig() {
    const area = document.getElementById('settings-area');
    area.innerHTML = '<div class="loading"><div class="spinner"></div> กำลังโหลด...</div>';
    let data;
    try {
        data = await api('/promo-manager');
    } catch (e) {
        area.innerHTML = `<div class="err">โหลดไม่สำเร็จ: ${e.message}</div>`;
        return;
    }
    window._configCache = data;

    // Group by category
    const byCat = {};
    data.forEach(c => { (byCat[c.category] = byCat[c.category] || []).push(c); });

    let html = '<div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem">';
    Object.keys(_CONFIG_GROUPS).forEach(cat => {
        if (!byCat[cat]) return;
        html += `<a href="#cfg-${cat}" class="btn btn-sm btn-outline">${_CONFIG_GROUPS[cat].icon} ${esc(_CONFIG_GROUPS[cat].label)}</a>`;
    });
    html += '</div>';

    Object.entries(byCat).forEach(([cat, configs]) => {
        const meta = _CONFIG_GROUPS[cat] || { icon: '⚙️', label: cat, desc: '' };
        html += `<div id="cfg-${cat}" style="margin-bottom:2rem"><h3 style="margin-bottom:0.5rem">${meta.icon} ${esc(meta.label)}</h3>`;
        html += `<div style="opacity:0.6;font-size:0.85rem;margin-bottom:0.7rem">${esc(meta.desc)}</div>`;
        html += '<div class="table-wrap"><table><thead><tr><th>Key</th><th>ค่า</th><th>คำอธิบาย</th><th></th></tr></thead><tbody>';
        configs.forEach(c => {
            const val = typeof c.value_json === 'object' ? JSON.stringify(c.value_json) : String(c.value_json);
            html += `<tr>
                <td><code style="font-size:0.85rem">${esc(c.config_key)}</code></td>
                <td><b>${esc(val)}</b></td>
                <td style="font-size:0.85rem;opacity:0.85">${esc(c.description || '')}</td>
                <td>${hasRole('owner') ? `<button class="btn btn-sm btn-outline" onclick="editConfig('${esc(c.config_key)}')">✏️</button>` : ''}</td>
            </tr>`;
        });
        html += '</tbody></table></div></div>';
    });

    html += '<div style="margin-top:1rem;padding:0.7rem;background:rgba(245,158,11,0.15);border-left:4px solid #f59e0b;border-radius:0.4rem;font-size:0.85rem">⚠️ แก้ <b>เวลา cron</b> หรือ <b>ระบบยศ</b> ต้อง <b>restart sales-bot</b> ถึงจะ apply (ไปที่ Settings → 🤖 บอท → กด Restart)</div>';

    area.innerHTML = html;
}

function editConfig(configKey) {
    const cfg = (window._configCache || []).find(c => c.config_key === configKey);
    if (!cfg) return;
    const val = typeof cfg.value_json === 'object' ? JSON.stringify(cfg.value_json, null, 2) : String(cfg.value_json);
    const isJson = typeof cfg.value_json === 'object';
    openModal('✏️ ' + cfg.config_key, `
        <div class="form-group"><label>ค่า ${isJson ? '(JSON)' : ''}</label>
            ${isJson 
                ? `<textarea id="cfg-val" rows="6" style="font-family:monospace;width:100%">${esc(val)}</textarea>`
                : `<input id="cfg-val" value="${esc(val)}">`}
        </div>
        <div class="form-group"><label>คำอธิบาย</label><input id="cfg-desc" value="${esc(cfg.description || '')}"></div>
        <div style="background:rgba(255,255,255,0.05);padding:0.6rem;border-radius:0.4rem;font-size:0.85rem;margin-bottom:0.7rem;opacity:0.85">Category: <code>${esc(cfg.category)}</code></div>
        <button class="btn btn-primary btn-full" onclick="saveConfig('${esc(configKey)}', ${isJson})">💾 บันทึก</button>
    `);
}

async function saveConfig(configKey, isJson) {
    const valStr = document.getElementById('cfg-val').value;
    const desc = document.getElementById('cfg-desc').value;
    let val;
    try {
        val = isJson ? JSON.parse(valStr) : (isNaN(Number(valStr)) ? valStr.replace(/^"|"$/g, '') : Number(valStr));
        if (typeof val === 'string' && (val === 'true' || val === 'false')) val = (val === 'true');
    } catch (e) {
        toast('JSON ไม่ถูกต้อง: ' + e.message, 'error');
        return;
    }
    try {
        await api(`/promo-manager/${encodeURIComponent(configKey)}`, {
            method: 'PATCH',
            body: JSON.stringify({ value_json: val, description: desc }),
        });
        toast('บันทึกแล้ว ✅', 'success');
        closeModal();
        loadSystemConfig();
    } catch (e) { toast(e.message, 'error'); }
}
