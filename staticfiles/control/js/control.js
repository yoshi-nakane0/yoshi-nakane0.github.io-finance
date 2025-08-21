// control/static/control/js/control.js
// Fed Rate Monitor Tool用JavaScript（テンプレートベース）

console.log('Control.js loaded');

// ページ読み込み完了時の処理
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, initializing...');
    
    // 日付カードのクリックイベントを設定
    setupDateCardClickEvents();
    
    // 更新ボタンのイベント設定
    setupRefreshButton();
    
    // Back to topボタンの設定
    setupBackToTopButton();
    
    console.log('Initialization complete');
});

// 日付カードのクリックイベントを設定
function setupDateCardClickEvents() {
    const dateCards = document.querySelectorAll('.date-card');
    
    dateCards.forEach(card => {
        card.addEventListener('click', function() {
            const selectedDate = this.getAttribute('data-date');
            console.log('Date card clicked:', selectedDate);
            
            // アクティブ状態を更新
            dateCards.forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            
            // テーブルを更新
            updateTableForDate(selectedDate);
        });
    });
}

// 指定された日付のテーブルデータを表示
function updateTableForDate(date) {
    console.log('Updating table for date:', date);
    
    const tbody = document.getElementById('fed-probabilities');
    if (!tbody) {
        console.error('Table body not found');
        return;
    }
    
    // まずテーブルを空にする
    tbody.innerHTML = '';
    
    // DjangoからのJSONデータを取得
    const djangoDataElement = document.getElementById('django-data');
    if (!djangoDataElement) {
        console.error('Django data element not found');
        tbody.innerHTML = `<tr><td colspan="4">データが見つかりません</td></tr>`;
        return;
    }
    
    let fedData;
    try {
        fedData = JSON.parse(djangoDataElement.getAttribute('data-fed-data'));
    } catch (e) {
        console.error('Failed to parse fed data:', e);
        tbody.innerHTML = `<tr><td colspan="4">データの解析に失敗しました</td></tr>`;
        return;
    }
    
    // 指定された日付のデータを取得
    const probabilities = fedData[date];
    
    if (!probabilities || probabilities.length === 0) {
        console.warn('No data found for date:', date);
        tbody.innerHTML = `<tr><td colspan="4">${date}のデータがありません</td></tr>`;
        return;
    }
    
    // 各確率データを行として追加
    probabilities.forEach((prob, index) => {
        console.log(`Processing row ${index}:`, prob);
        
        const tr = document.createElement('tr');
        tr.className = `prob-row ${prob.type}`;
        tr.setAttribute('data-date', date);
        
        // % 記号を追加（"—"でない場合のみ）
        const formatPercent = (value) => {
            if (value === '—' || value.includes('%')) {
                return value;
            }
            return value + '%';
        };
        
        tr.innerHTML = `
            <td class="rate-cell">${prob.range}</td>
            <td class="prob-cell ${prob.type}">${formatPercent(prob.current)}</td>
            <td class="prob-cell ${prob.type}">${formatPercent(prob.oneDay)}</td>
            <td class="prob-cell ${prob.type}">${formatPercent(prob.oneWeek)}</td>
        `;
        
        tbody.appendChild(tr);
    });
    
    console.log(`Table updated with ${probabilities.length} rows for ${date}`);
}

// 更新ボタンの設定
function setupRefreshButton() {
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function() {
            console.log('Refresh button clicked');
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '🔄 更新中...';
            
            // ページをリロードして最新データを取得
            setTimeout(() => {
                window.location.reload();
            }, 500);
        });
    }
}

// Back to topボタンの設定
function setupBackToTopButton() {
    const backToTopBtn = document.getElementById('back-to-top');
    if (backToTopBtn) {
        window.addEventListener('scroll', function() {
            if (window.pageYOffset > 100) {
                backToTopBtn.style.display = 'block';
            } else {
                backToTopBtn.style.display = 'none';
            }
        });
        
        backToTopBtn.addEventListener('click', function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }
}

console.log('Control.js setup complete');