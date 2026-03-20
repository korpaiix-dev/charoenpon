/* ============================================
   เจริญพร Dashboard — SPA Application
   ============================================ */

// ========== STATE ==========
let token = localStorage.getItem('token');
let admin = JSON.parse(localStorage.getItem('admin') || 'null');
let currentPage = 'dashboard';
let charts = {};

const ROLE_LEVELS = { owner: 100, super_admin: 75, admin: 50, moderator: 10 };

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

function logout() {
    if (token) {
        fetch('/api/auth/logout', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        }).catch(() => {});
    }
    token = null; admin = null;
    localStorage.removeItem('token');
    localStorage.removeItem('admin');
    if (alertInterval) { clearInterval(alertInterval); alertInterval = null; }
    document.getElementById('app').classList.add('hidden');
    document.getElementById('login-page').classList.remove('hidden');
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

// ========== ALERT POLLING ==========
let alertInterval;
function startAlertPolling() {
    checkAlerts();
    alertInterval = setInterval(checkAlerts, 30000);
}
async function checkAlerts() {
    try {
        const data = await api('/dashboard/alerts');
        const count = data.pending_slips || 0;
        const badge = document.getElementById('alert-badge');
        const countEl = document.getElementById('alert-count');
        if (count > 0) {
            badge.classList.remove('hidden');
            countEl.textContent = count;
        } else {
            badge.classList.add('hidden');
        }
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
        const [summary, members, flashSale, alerts] = await Promise.all([
            api('/dashboard/summary'),
            api('/dashboard/members-stats'),
            api('/dashboard/flash-sale-status'),
            api('/dashboard/alerts'),
        ]);
        
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
        if (!alertItems) alertItems = '<div class="alert-box-item" style="color:var(--success);">✅ ไม่มี alert</div>';

        content.innerHTML = `
            <div class="cards-grid">
                <div class="card"><div class="card-label">วันนี้</div><div class="card-value">${fmtBaht(summary.today)}</div>${changeArrow(summary.today_change)}</div>
                <div class="card"><div class="card-label">สัปดาห์นี้</div><div class="card-value">${fmtBaht(summary.week)}</div>${changeArrow(summary.week_change)}</div>
                <div class="card"><div class="card-label">เดือนนี้</div><div class="card-value">${fmtBaht(summary.month)}</div>${changeArrow(summary.month_change)}</div>
            </div>
            <div class="card card-full"><div class="card-label">📈 กราฟรายได้ 30 วัน</div><div class="chart-container"><canvas id="revenue-chart"></canvas></div></div>
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
        `;
        
        // Revenue chart
        const chartData = await api('/dashboard/revenue-chart?days=30');
        if (chartData.length > 0) {
            const ctx = document.getElementById('revenue-chart');
            charts.revenue = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: chartData.map(d => d.date.slice(5)),
                    datasets: [{
                        label: 'รายได้ (฿)',
                        data: chartData.map(d => d.revenue),
                        borderColor: '#00d4ff',
                        backgroundColor: 'rgba(0, 212, 255, 0.1)',
                        fill: true, tension: 0.4, pointRadius: 3,
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
        }
    } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${err.message}</p></div>`;
    }
}

// ========== PAGE: CUSTOMERS ==========
let customerSearch = '', customerFilter = 'all', customerPage = 1;
async function renderCustomers() {
    const content = document.getElementById('page-content');
    const broadcastBtn = hasRole('admin') ? `<button class="btn btn-primary" onclick="showBroadcastModal()" style="margin-bottom:1rem;">📢 Broadcast</button>` : '';
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
        <div id="bc-result" style="display:none;margin-bottom:1rem;"></div>
        <button class="btn btn-primary btn-full" id="bc-send-btn" onclick="doBroadcast()">📩 ส่ง Broadcast</button>
    `);
    updateBroadcastCount();
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
    
    const labels = { all: 'ทุกคน', active: 'VIP Active', expired: 'Expired', trial: 'Trial' };
    if (!confirm(`📢 ยืนยันส่ง Broadcast ไปยัง "${labels[target]}"?\n\nข้อความ:\n${message.slice(0, 200)}`)) return;
    
    const btn = document.getElementById('bc-send-btn');
    const result = document.getElementById('bc-result');
    btn.disabled = true;
    btn.textContent = '⏳ กำลังส่ง...';
    result.style.display = 'none';
    
    try {
        const data = await api('/customers/broadcast', {
            method: 'POST',
            body: JSON.stringify({ message, target, parse_mode: 'HTML' }),
        });
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
        result.innerHTML = `<div class="alert-box"><div class="alert-box-item" style="color:var(--error);">❌ ${e.message}</div></div>`;
        btn.disabled = false;
        btn.textContent = '📩 ส่ง Broadcast';
        toast(e.message, 'error');
    }
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
                <td>${u.username ? '@'+u.username : u.first_name || '-'}</td>
                <td style="font-family:var(--font-mono);font-size:0.8rem;">${u.telegram_id}</td>
                <td>${u.package_name || '-'}</td>
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
        
        let payHtml = payments.map(p => `<tr><td>${fmtDate(p.created_at)}</td><td>${fmtBaht(p.amount)}</td><td>${p.method}</td><td>${statusBadge(p.status)}</td></tr>`).join('');
        let groupsHtml = groups.map(g => `<span class="status-badge status-active">${g.slug}</span>`).join(' ') || '-';
        
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
        
        openModal(`👤 ${u.username ? '@'+u.username : u.first_name || 'User'} (ID: ${u.telegram_id})`, `
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
        const checkboxes = groups.map(g => `<label style="display:block;margin:0.3rem 0;"><input type="checkbox" class="kick-group" value="${g.id}"> ${g.slug} — ${g.title}</label>`).join('');
        openModal('🔨 เตะออกจากกลุ่ม', `${checkboxes}<button class="btn btn-warning btn-full" style="margin-top:1rem;" onclick="doKick(${userId})">ยืนยันเตะ</button>`);
    } else if (action === 'upgrade') {
        const pkgs = await api('/settings/packages');
        const opts = pkgs.map(p => `<option value="${p.id}">${p.name} (${fmtBaht(p.price)})</option>`).join('');
        openModal('🆙 อัพเกรด', `
            <div class="form-group"><label>แพ็กเกจใหม่</label><select id="upgrade-pkg">${opts}</select></div>
            <button class="btn btn-primary btn-full" onclick="doUpgrade(${userId})">ยืนยันอัพเกรด</button>
        `);
    }
}

async function doExtend(uid) {
    try {
        await api(`/customers/${uid}/extend`, { method: 'POST', body: JSON.stringify({ days: parseInt(document.getElementById('extend-days').value) }) });
        toast('ต่อเวลาสำเร็จ', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
}
async function doDM(uid) {
    try {
        await api(`/customers/${uid}/dm`, { method: 'POST', body: JSON.stringify({ message: document.getElementById('dm-message').value }) });
        toast('ส่ง DM แล้ว', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
}
async function doBan(uid) {
    try {
        await api(`/customers/${uid}/ban`, { method: 'POST', body: JSON.stringify({ reason: document.getElementById('ban-reason').value }) });
        toast('แบนแล้ว', 'success'); closeModal(); loadCustomers();
    } catch (e) { toast(e.message, 'error'); }
}
async function doKick(uid) {
    const ids = [...document.querySelectorAll('.kick-group:checked')].map(c => parseInt(c.value));
    if (!ids.length) { toast('เลือกกลุ่มก่อน', 'error'); return; }
    try {
        await api(`/customers/${uid}/kick`, { method: 'POST', body: JSON.stringify({ group_ids: ids }) });
        toast('เตะแล้ว', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
}
async function doUpgrade(uid) {
    try {
        await api(`/customers/${uid}/upgrade`, { method: 'POST', body: JSON.stringify({ package_id: parseInt(document.getElementById('upgrade-pkg').value) }) });
        toast('อัพเกรดสำเร็จ', 'success'); closeModal();
    } catch (e) { toast(e.message, 'error'); }
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
            html += `<div class="pending-card">
                <div class="pending-info">
                    <span>👤 ${p.username ? '@'+p.username : p.first_name || p.telegram_id}</span>
                    <span style="font-weight:600;">${fmtBaht(p.amount)}</span>
                    <span style="color:var(--text-muted);font-size:0.8rem;">${fmtDateTime(p.created_at)}</span>
                    ${p.slip_url ? `<button class="btn btn-sm btn-outline" onclick="window.open('${p.slip_url}')">🖼 ดูสลิป</button>` : ''}
                </div>
                <div class="btn-group">
                    <button class="btn btn-sm btn-success" onclick="approvePayment(${p.id})">✅ อนุมัติ</button>
                    <button class="btn btn-sm btn-danger" onclick="rejectPayment(${p.id})">❌ ปฏิเสธ</button>
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
                    <span>👤 ${p.username ? '@'+p.username : p.first_name || p.telegram_id}</span>
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
    if (!confirm('อนุมัติสลิปนี้?')) return;
    try {
        await api(`/payments/${id}/approve`, { method: 'POST' });
        toast('อนุมัติแล้ว', 'success');
        loadPendingSlips(); loadPayments(); checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
}

async function rejectPayment(id) {
    const reason = prompt('เหตุผลปฏิเสธ:');
    if (reason === null) return;
    try {
        await api(`/payments/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) });
        toast('ปฏิเสธแล้ว', 'success');
        loadPendingSlips(); loadPayments(); checkAlerts();
    } catch (e) { toast(e.message, 'error'); }
}

async function loadPayments(page) {
    if (page) financePage = page;
    try {
        const data = await api(`/payments?page=${financePage}&per_page=25&status=${financeFilter}`);
        let html = '<div class="table-wrap"><table><thead><tr><th>วันที่</th><th>ชื่อ</th><th>จำนวน</th><th>วิธี</th><th>แพ็กเกจ</th><th>สถานะ</th></tr></thead><tbody>';
        data.items.forEach(p => {
            html += `<tr>
                <td>${fmtDateTime(p.created_at)}</td>
                <td>${p.username ? '@'+p.username : p.first_name || '-'}</td>
                <td style="font-weight:600;">${fmtBaht(p.amount)}</td>
                <td>${p.method}</td>
                <td>${p.package_name}</td>
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
let promoTab = 'flash';
async function renderPromotions() {
    const content = document.getElementById('page-content');
    content.innerHTML = `
        <div class="tabs">
            <div class="tab ${promoTab==='flash'?'active':''}" onclick="promoTab='flash';renderPromotions()">⚡ Flash Sale</div>
            <div class="tab ${promoTab==='code'?'active':''}" onclick="promoTab='code';renderPromotions()">🎟 Promo Code</div>
            <div class="tab ${promoTab==='scheduled'?'active':''}" onclick="promoTab='scheduled';renderPromotions()">📅 ตั้งเวลาโปรโมท</div>
        </div>
        <div id="promo-content"><div class="loading"><div class="spinner"></div></div></div>
    `;
    if (promoTab === 'flash') loadFlashSales();
    else if (promoTab === 'code') loadPromoCodes();
    else loadScheduledPromos();
}

async function loadFlashSales() {
    try {
        const data = await api('/flash-sales');
        let html = `<button class="btn btn-primary" onclick="showFlashSaleForm()" style="margin-bottom:1rem;">+ สร้าง Flash Sale ใหม่</button>`;
        html += '<div class="table-wrap"><table><thead><tr><th>ชื่อ</th><th>ราคา</th><th>Slot</th><th>Sold</th><th>เริ่ม</th><th>สิ้นสุด</th><th>สถานะ</th><th></th></tr></thead><tbody>';
        data.forEach(s => {
            html += `<tr>
                <td>${s.name}</td><td>${fmtBaht(s.flash_price)}</td><td>${s.total_slots}</td><td>${s.sold_slots}</td>
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

function showFlashSaleForm() {
    openModal('⚡ สร้าง Flash Sale', `
        <div class="form-group"><label>ชื่อ</label><input id="fs-name" placeholder="ชื่อ Flash Sale"></div>
        <div class="form-row">
            <div class="form-group"><label>ราคา Flash</label><input id="fs-price" type="number" placeholder="199"></div>
            <div class="form-group"><label>ราคาเดิม</label><input id="fs-orig" type="number" placeholder="300"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>Package ID</label><input id="fs-pkg" type="number" value="1"></div>
            <div class="form-group"><label>จำนวน Slot</label><input id="fs-slots" type="number" value="30"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>เริ่ม</label><input id="fs-start" type="datetime-local"></div>
            <div class="form-group"><label>สิ้นสุด</label><input id="fs-end" type="datetime-local"></div>
        </div>
        <button class="btn btn-primary btn-full" onclick="createFlashSale()">💾 สร้าง</button>
    `);
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
                <td style="font-family:var(--font-mono);color:var(--primary);">${c.code}</td>
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
            html += `<tr><td>${s.name}</td><td>${fmtDateTime(s.scheduled_at)}</td><td>${s.repeat_type}</td>
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
            html += `<tr><td>${i+1}</td><td>${c.file_type}</td><td style="font-family:var(--font-mono);font-size:0.75rem;">${(c.file_id || '').slice(0,30)}...</td><td>${fmtDateTime(c.created_at)}</td>
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
        progress.innerHTML = `<div style="color:var(--primary);font-size:0.85rem;">⏳ กำลังอัพโหลด ${file.name} (${i+1}/${files.length})...</div>`;
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
            html += `<tr><td>${fmtDateTime(s.scheduled_at)}</td><td>${s.group_slug}</td><td>${s.content_type}</td>
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
            let html = `<div class="category-section"><div class="category-header">${emoji} ${title} <span class="category-count">${groups.length}</span>
                <button class="btn btn-sm btn-primary" style="margin-left:auto;" onclick="showAddGroupForm('${category}')">+ เพิ่มกลุ่ม</button>
            </div>`;
            if (!groups.length) { html += '<div class="empty-state" style="padding:1rem;">ไม่มีกลุ่ม</div></div>'; return html; }
            html += '<div class="table-wrap"><table><thead><tr><th>Slug</th><th>ชื่อกลุ่ม</th><th>Chat ID</th><th>Tier</th><th>สถานะ</th><th></th></tr></thead><tbody>';
            groups.forEach(g => {
                html += `<tr>
                    <td style="font-family:var(--font-mono);color:var(--primary);">${g.slug}</td>
                    <td>${g.title}</td>
                    <td style="font-family:var(--font-mono);font-size:0.8rem;">${g.chat_id}</td>
                    <td>${g.min_tier}</td>
                    <td>${g.is_active ? '<span style="color:var(--success)">Active</span>' : 'Off'}</td>
                    <td><div class="btn-group">
                        <button class="btn btn-sm btn-outline" onclick="showEditGroupForm(${g.id},'${g.slug}','${g.title.replace(/'/g,"&#39;")}',${g.chat_id},'${g.min_tier}',${g.is_active})">✏️</button>
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
        
    } catch (e) { content.innerHTML = `<div class="empty-state">${e.message}</div>`; }
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
        <div class="form-group"><label>ชื่อกลุ่ม</label><input id="egrp-title" value="${title}"></div>
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
            html += `<tr><td>${m.username ? '@'+m.username : m.first_name || '-'}</td><td>${m.telegram_id}</td><td>${m.package_name}</td><td>${fmtDate(m.end_date)}</td></tr>`;
        });
        html += '</tbody></table></div>';
        openModal('📱 สมาชิกกลุ่ม', html);
    } catch (e) { toast(e.message, 'error'); }
}

async function genInviteLink(id) {
    try {
        const result = await api(`/groups/${id}/invite-link`, { method: 'POST' });
        const link = result?.result?.invite_link || 'ไม่สามารถสร้าง link ได้';
        openModal('🔗 Invite Link', `<div style="word-break:break-all;font-family:var(--font-mono);color:var(--primary);padding:1rem;background:var(--bg);border-radius:var(--radius-sm);">${link}</div>
            <button class="btn btn-primary btn-full" style="margin-top:1rem;" onclick="navigator.clipboard.writeText('${link}');toast('คัดลอกแล้ว','success')">📋 Copy</button>`);
    } catch (e) { toast(e.message, 'error'); }
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
                <td>${m.display_name}</td>
                <td style="font-family:var(--font-mono);">${m.telegram_id}</td>
                <td>${roleIcon} ${m.role}</td>
                <td>${m.is_active ? '<span style="color:var(--success)">🟢</span>' : '<span style="color:var(--error)">🔴</span>'}</td>
                <td>${fmtDateTime(m.last_login_at)}</td>
                <td>${canEdit ? `<div class="btn-group"><button class="btn btn-sm btn-outline" onclick="showEditTeam(${m.id},'${m.display_name}','${m.role}',${m.is_active})">✏️</button><button class="btn btn-sm btn-outline" onclick="showTeamActivity(${m.id})">📋</button></div>` : (m.role !== 'owner' ? `<button class="btn btn-sm btn-outline" onclick="showTeamActivity(${m.id})">📋</button>` : '')}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
        content.innerHTML = html;
    } catch (e) { content.innerHTML = `<div class="empty-state">${e.message}</div>`; }
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
    try {
        await api('/team', { method: 'POST', body: JSON.stringify({
            telegram_id: parseInt(document.getElementById('tm-tid').value),
            display_name: document.getElementById('tm-name').value,
            password: document.getElementById('tm-pwd').value,
            role: document.getElementById('tm-role').value,
        })});
        toast('เพิ่มทีมงานแล้ว', 'success'); closeModal(); renderTeam();
    } catch (e) { toast(e.message, 'error'); }
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
            html += `<tr><td>${fmtDateTime(a.created_at)}</td><td>${a.action}</td><td>${a.entity_type || '-'}</td><td style="font-size:0.8rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;">${details}</td></tr>`;
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
            html += `<tr><td>${p.name}</td><td>${fmtBaht(p.price)}</td><td>${p.duration_days}</td><td>${p.tier}</td>
                <td>${p.is_active ? '<span style="color:var(--success)">Active</span>' : 'Off'}</td>
                <td>${hasRole('owner') ? `<button class="btn btn-sm btn-outline" onclick="editPkg(${p.id},'${p.name}',${p.price},${p.duration_days})">✏️</button>` : ''}</td></tr>`;
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
        <div class="form-group"><label>ชื่อ</label><input id="epkg-name" value="${name}"></div>
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
        const [kpi, funnel, weekly, insights] = await Promise.all([
            api('/marketing/kpi?days=30'),
            api('/marketing/funnel?days=30'),
            api('/marketing/weekly-comparison'),
            api('/marketing/ai-insights'),
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

            <div class="card card-full" style="margin-top:1.5rem;">
                <div class="card-label">🤖 AI Action Items</div>
                <div style="padding:1rem;white-space:pre-wrap;color:var(--text);font-size:0.9rem;">${insights.insights || 'ยังไม่มี'}</div>
                ${insights.date ? `<div style="font-size:0.75rem;color:var(--text-dim);padding:0 1rem 1rem;">อัพเดต: ${insights.date}</div>` : ''}
            </div>
        `;

        // Weekly chart
        if (weekly.length >= 3) {
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
            if (weeklyEl) weeklyEl.parentElement.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);font-size:0.9rem;">📊 ยังมีข้อมูลน้อย รอสะสมอย่างน้อย 3 สัปดาห์</div>';
        }
    } catch (e) { content.innerHTML = `<div class="empty-state"><div class="icon">❌</div><p>${e.message}</p></div>`; }
}

// ========== INIT ==========
if (token && admin) {
    showApp();
} else {
    document.getElementById('login-page').classList.remove('hidden');
}
