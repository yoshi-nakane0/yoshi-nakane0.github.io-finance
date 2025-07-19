// control/static/control/js/control.js
// Fed Rate Monitor Tool用JavaScript

// FOMC会合日程リスト（順番重要）
const fomcDates = [
    '2025-07-30',
    '2025-09-17', 
    '2025-10-29',
    '2025-12-10',
    '2026-01-26',
    '2026-03-18',
    '2026-04-29',
    '2026-06-17'
];

// 現在の日付から有効な4つの日付を取得
function getActiveDates() {
    const today = new Date();
    const activeDates = [];
    
    for (let date of fomcDates) {
        const meetingDate = new Date(date);
        const daysPassed = Math.floor((today - meetingDate) / (1000 * 60 * 60 * 24));
        
        // その日が過ぎて2日後まで表示
        if (daysPassed <= 2) {
            activeDates.push(date);
        }
        
        // 4つまでに制限
        if (activeDates.length >= 4) {
            break;
        }
    }
    
    // 4つに満たない場合は"0000-00-00"で埋める
    while (activeDates.length < 4) {
        activeDates.push('0000-00-00');
    }
    
    return activeDates;
}

// 日付から月名を取得
function getMonthName(dateString) {
    if (dateString === '0000-00-00') return '終了';
    const date = new Date(dateString);
    const month = date.getMonth() + 1;
    return month + '月会合';
}

// カレンダーの動的生成
function generateCalendars() {
    const activeDates = getActiveDates();
    
    // Fed Rate Monitor Tool カレンダー生成
    const fedMonitorContainer = document.getElementById('fed-monitor-dates');
    fedMonitorContainer.innerHTML = '';
    
    activeDates.forEach((date, index) => {
        const card = document.createElement('div');
        card.className = `date-card ${index === 0 ? 'active' : ''}`;
        card.setAttribute('data-date', date);
        
        if (date === '0000-00-00') {
            card.innerHTML = `
                <div class="date-icon">❌</div>
                <h3>0000-00-00</h3>
                <p>終了</p>
            `;
        } else {
            card.innerHTML = `
                <div class="date-icon">📅</div>
                <h3>${date}</h3>
                <p>${getMonthName(date)}</p>
            `;
        }
        
        fedMonitorContainer.appendChild(card);
    });
    
    // FedWatch カレンダー生成
    const fomcContainer = document.getElementById('fomc-meeting-dates');
    fomcContainer.innerHTML = '';
    
    activeDates.forEach((date, index) => {
        const card = document.createElement('div');
        card.className = `date-card ${index === 0 ? 'active' : ''}`;
        card.setAttribute('data-date', date);
        
        if (date === '0000-00-00') {
            card.innerHTML = `
                <div class="date-icon">❌</div>
                <h3>0000-00-00</h3>
                <p>終了</p>
            `;
        } else {
            card.innerHTML = `
                <div class="date-icon">📅</div>
                <h3>${date}</h3>
                <p>${getMonthName(date)}</p>
            `;
        }
        
        fomcContainer.appendChild(card);
    });
    
    // 初期データ表示
    const firstDate = activeDates[0];
    if (window.fedMonitorData && window.fedMonitorData[firstDate]) {
        updateFedMonitorTable(window.fedMonitorData[firstDate].probabilities);
    }
    if (window.fomcData && window.fomcData[firstDate]) {
        updateTable(window.fomcData[firstDate].probabilities);
    }
}

// カードクリックイベントを動的に設定
function setupCardClickEvents() {
    // Fed Monitor Tool のカードクリック
    const fedMonitorCards = document.querySelectorAll('#fed-monitor-dates .date-card');
    
    fedMonitorCards.forEach(card => {
        card.addEventListener('click', function() {
            // アクティブ状態を切り替え
            fedMonitorCards.forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            
            // 選択された日程のデータを取得
            const selectedDate = this.getAttribute('data-date');
            const data = window.fedMonitorData[selectedDate];
            
            if (data) {
                // Fed Monitor テーブルデータを更新
                updateFedMonitorTable(data.probabilities);
            }
        });
    });

    // FedWatch のカードクリック
    const dateCards = document.querySelectorAll('#fomc-meeting-dates .date-card');
    
    dateCards.forEach(card => {
        card.addEventListener('click', function() {
            // アクティブ状態を切り替え
            dateCards.forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            
            // 選択された日程のデータを取得
            const selectedDate = this.getAttribute('data-date');
            const data = window.fomcData[selectedDate];
            
            if (data) {
                // テーブルデータを更新
                updateTable(data.probabilities);
            }
        });
    });
}

function updateFedMonitorTable(probabilities) {
    const tbody = document.getElementById('fed-monitor-probabilities');
    tbody.innerHTML = '';
    
    probabilities.forEach(prob => {
        const row = document.createElement('tr');
        row.className = prob.type + '-row';
        
        const currentClass = prob.type === 'positive' ? 'positive-text' : 'negative-text';
        
        row.innerHTML = `
            <td class="sector-name-cell">${prob.range}</td>
            <td class="sector-change-cell ${currentClass}">${prob.current}</td>
            <td class="sector-change-cell">${prob.oneDay}</td>
            <td class="sector-change-cell">${prob.oneWeek}</td>
        `;
        
        tbody.appendChild(row);
    });
}

function updateTable(probabilities) {
    const tbody = document.getElementById('fomc-probabilities');
    tbody.innerHTML = '';
    
    probabilities.forEach(prob => {
        const row = document.createElement('tr');
        row.className = prob.type + '-row';
        
        const currentClass = prob.type === 'positive' ? 'positive-text' : 'negative-text';
        
        row.innerHTML = `
            <td class="sector-name-cell">${prob.range}</td>
            <td class="sector-change-cell ${currentClass}">${prob.current}</td>
            <td class="sector-change-cell">${prob.oneWeek}</td>
            <td class="sector-change-cell">${prob.oneMonth}</td>
        `;
        
        tbody.appendChild(row);
    });
}

// Refresh button functionality
function refreshData() {
    const refreshBtn = document.getElementById('refresh-btn');
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '🔄 更新中...';

    // シミュレート: 実際のAPIコールの代わりに更新時間を変更
    setTimeout(() => {
        const now = new Date();
        const timeString = now.toLocaleString('ja-JP', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        }).replace(/\//g, '-');
        
        document.getElementById('update-time').textContent = timeString;
        
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '🔄 更新';
    }, 1500);
}

// Back to top button script
function initBackToTop() {
    const backToTopButton = document.getElementById("back-to-top");

    window.onscroll = function() {
        if (document.body.scrollTop > 20 || document.documentElement.scrollTop > 20) {
            backToTopButton.style.display = "block";
        } else {
            backToTopButton.style.display = "none";
        }
    };

    backToTopButton.addEventListener("click", () => {
        document.body.scrollTop = 0; // For Safari
        document.documentElement.scrollTop = 0; // For Chrome, Firefox, IE and Opera
    });
}

// 初期化
document.addEventListener('DOMContentLoaded', function() {
    // カレンダー生成
    generateCalendars();
    
    // イベントリスナーを設定
    setupCardClickEvents();
    
    // Refresh button functionality
    document.getElementById('refresh-btn').addEventListener('click', function() {
        refreshData();
    });
    
    // Back to top button
    initBackToTop();
});