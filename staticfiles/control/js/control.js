// control/static/control/js/control.js
// Fed Rate Monitor Tool用JavaScript

// FOMC会合日程を動的に管理するグローバル変数
let fomcDates = [];

// CSVデータからFOMC会合日程を抽出し、自動更新する
function updateFomcDatesFromCSV() {
    if (!csvData || csvData.length === 0) {
        console.warn('CSV data not available for updating FOMC dates');
        return;
    }
    
    // CSVからユニークな会合日を抽出
    const uniqueDates = [...new Set(csvData.map(row => row.Meeting))]
        .filter(date => date && date !== '') // 空の値を除外
        .sort(); // 日付順にソート
    
    console.log('Extracted meeting dates from CSV:', uniqueDates);
    
    const today = new Date();
    const filteredDates = [];
    
    // 現在日以降の日付をフィルタリング（過去2日までは表示）
    uniqueDates.forEach(dateString => {
        const meetingDate = new Date(dateString);
        const daysPassed = Math.floor((today - meetingDate) / (1000 * 60 * 60 * 24));
        
        // 会合が終了してから3日までは表示し、それ以外は除外
        if (daysPassed <= 3) {
            filteredDates.push(dateString);
        } else {
            console.log(`Removing expired meeting date: ${dateString} (${daysPassed} days ago)`);
        }
    });
    
    // 既存の日付と比較して変更があるか確認
    const hasChanges = JSON.stringify(fomcDates) !== JSON.stringify(filteredDates);
    
    if (hasChanges) {
        console.log('FOMC dates updated:', {
            old: fomcDates,
            new: filteredDates
        });
        fomcDates = filteredDates;
        return true; // 変更あり
    }
    
    return false; // 変更なし
}

// 現在の日付から有効な4つの日付を取得（更新版）
function getActiveDates() {
    // FOMC日程が空の場合はまずCSVから更新を試みる
    if (fomcDates.length === 0) {
        updateFomcDatesFromCSV();
    }
    
    const activeDates = [];
    
    // 有効な日付を収集（最大4個）
    for (let date of fomcDates) {
        if (date && date !== '0000-00-00') {
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
    
    console.log('Active meeting dates:', activeDates);
    return activeDates;
}

// 日付から月名を取得
function getMonthName(dateString) {
    if (dateString === '0000-00-00') return '終了';
    const date = new Date(dateString);
    const month = date.getMonth() + 1;
    return month + '月会合';
}

// CSVデータをグローバル変数として保存
let csvData = [];

// CSVファイルを読み込み、FOMC日程を自動更新する関数
async function loadCSVData() {
    try {
        // HTMLから渡されたCSVパスを使用、フォールバックも設定
        const csvPath = window.csvPath || '/static/control/data/fed.csv';
        console.log('Loading CSV from:', csvPath);
        
        const response = await fetch(csvPath);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        let csvText = await response.text();
        
        // BOM除去（UTF-8のBOMがある場合）
        if (csvText.charCodeAt(0) === 0xFEFF) {
            csvText = csvText.slice(1);
        }
        
        const lines = csvText.trim().split('\n');
        if (lines.length === 0) {
            throw new Error('CSV file is empty');
        }
        
        // ヘッダー行を処理
        const headers = lines[0].split(',').map(h => h.trim());
        console.log('CSV headers:', headers);
        
        csvData = [];
        for (let i = 1; i < lines.length; i++) {
            const line = lines[i].trim();
            if (line) {
                const values = line.split(',');
                const row = {};
                headers.forEach((header, index) => {
                    row[header] = values[index] ? values[index].trim() : '';
                });
                csvData.push(row);
            }
        }
        
        console.log('CSV data loaded successfully:', csvData.length, 'rows');
        console.log('Sample data:', csvData[0]);
        
        // CSVデータ読み込み後、FOMC日程を自動更新
        const datesUpdated = updateFomcDatesFromCSV();
        if (datesUpdated) {
            console.log('FOMC meeting dates were automatically updated from CSV data');
        }
        
        return csvData;
    } catch (error) {
        console.error('Error loading CSV:', error);
        // フォールバック：元のパスも試してみる
        try {
            const response2 = await fetch('/staticfiles/control/data/fed.csv');
            if (response2.ok) {
                let csvText = await response2.text();
                if (csvText.charCodeAt(0) === 0xFEFF) {
                    csvText = csvText.slice(1);
                }
                const lines = csvText.trim().split('\n');
                const headers = lines[0].split(',').map(h => h.trim());
                csvData = [];
                for (let i = 1; i < lines.length; i++) {
                    const line = lines[i].trim();
                    if (line) {
                        const values = line.split(',');
                        const row = {};
                        headers.forEach((header, index) => {
                            row[header] = values[index] ? values[index].trim() : '';
                        });
                        csvData.push(row);
                    }
                }
                console.log('CSV loaded from fallback path');
                
                // フォールバックでもFOMC日程を更新
                updateFomcDatesFromCSV();
                return csvData;
            }
        } catch (fallbackError) {
            console.error('Fallback also failed:', fallbackError);
        }
        return [];
    }
}

// カレンダーの動的生成
function generateCalendars() {
    const activeDates = getActiveDates();
    
    // カレンダー生成（1つのみ）
    const fedContainer = document.getElementById('fed-meeting-dates');
    fedContainer.innerHTML = '';
    
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
        
        fedContainer.appendChild(card);
    });
    
    // 初期データ表示
    const firstDate = activeDates[0];
    console.log('Initial date for table:', firstDate);
    if (csvData && csvData.length > 0) {
        updateTableFromCSV(firstDate);
    } else {
        console.log('CSV data not ready, will load on page ready');
    }
}

// カードクリックイベントを動的に設定
function setupCardClickEvents() {
    // カードクリック
    const dateCards = document.querySelectorAll('#fed-meeting-dates .date-card');
    
    dateCards.forEach(card => {
        card.addEventListener('click', function() {
            // アクティブ状態を切り替え
            dateCards.forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            
            // 選択された日程のデータを取得
            const selectedDate = this.getAttribute('data-date');
            updateTableFromCSV(selectedDate);
        });
    });
}

// CSVデータからテーブルを更新する関数
function updateTableFromCSV(selectedDate) {
    const tbody = document.getElementById('fed-probabilities');
    tbody.innerHTML = '';
    
    console.log('Updating table for date:', selectedDate);
    console.log('Available CSV data:', csvData);
    
    if (!csvData || csvData.length === 0) {
        console.error('No CSV data available');
        tbody.innerHTML = '<tr><td colspan="5">データが読み込まれていません</td></tr>';
        return;
    }
    
    // CSVから選択された日付のデータを取得
    const dateData = csvData.filter(row => {
        console.log('Comparing:', row.Meeting, 'with', selectedDate);
        return row.Meeting === selectedDate;
    });
    
    console.log('Filtered data for', selectedDate, ':', dateData);
    
    if (dateData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5">${selectedDate}のデータがありません</td></tr>`;
        return;
    }
    
    dateData.forEach(row => {
        const tr = document.createElement('tr');
        tr.className = 'data-row';
        
        // データの値を取得（%記号は除去）
        const current = parseFloat(row.Current) || 0;
        const oneDay = parseFloat(row['1D (30 7 2025)(%)']) || 0;
        const oneWeek = parseFloat(row['1W (25 7 2025)(%)']) || 0;
        const oneMonth = parseFloat(row['1M (1 7 2025)(%)']) || 0;
        
        console.log('Row data:', {
            TargetRate: row.TargetRate,
            Current: current,
            OneDay: oneDay,
            OneWeek: oneWeek,
            OneMonth: oneMonth
        });
        
        // 現在の値に基づいてクラス設定
        const currentClass = current > 25 ? 'positive-text' : 'negative-text';
        
        tr.innerHTML = `
            <td class="sector-name-cell">${row.TargetRate || 'N/A'}</td>
            <td class="sector-change-cell ${currentClass}">${current.toFixed(2)}%</td>
            <td class="sector-change-cell">${oneDay.toFixed(2)}%</td>
            <td class="sector-change-cell">${oneWeek.toFixed(2)}%</td>
            <td class="sector-change-cell">${oneMonth.toFixed(2)}%</td>
        `;
        
        tbody.appendChild(tr);
    });
}

// Refresh button functionality - CSVファイルを再読み込み、日程自動更新
function refreshData() {
    const refreshBtn = document.getElementById('refresh-btn');
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '🔄 更新中...';

    // CSVファイルを再読み込み、FOMC日程も自動更新
    loadCSVData().then(() => {
        // 日程が更新された可能性があるので、カレンダーを再生成
        generateCalendars();
        
        // イベントリスナーを再設定
        setupCardClickEvents();
        
        // 初期データ表示
        const activeDates = getActiveDates();
        const firstDate = activeDates[0];
        updateTableFromCSV(firstDate);
        
        // 更新時間を表示
        document.getElementById('update-time').textContent = new Date().toLocaleString('ja-JP');
        
        console.log('データ更新完了 - FOMC日程も自動更新されました');
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '🔄 更新';
    }).catch(error => {
        console.error('Error:', error);
        alert('データ更新に失敗しました: ' + error.message);
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '🔄 更新';
    });
}


// Get CSRF token from cookies
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
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

// 定期的なFOMC日程チェック（日が変わったときに実行）
let lastCheckDate = null;

function checkAndUpdateDatesIfNeeded() {
    const today = new Date().toDateString();
    
    // 日付が変わった場合にのみFOMC日程をチェック
    if (lastCheckDate !== today) {
        console.log('Date changed from', lastCheckDate, 'to', today, '- Checking FOMC dates');
        lastCheckDate = today;
        
        const datesUpdated = updateFomcDatesFromCSV();
        if (datesUpdated) {
            console.log('FOMC dates updated due to date change - regenerating calendars');
            generateCalendars();
            setupCardClickEvents();
            
            // 初期データ表示
            const activeDates = getActiveDates();
            const firstDate = activeDates[0];
            updateTableFromCSV(firstDate);
        }
    }
}

// 初期化
document.addEventListener('DOMContentLoaded', async function() {
    console.log('DOM loaded, starting initialization...');
    
    // CSVデータを読み込み
    await loadCSVData();
    
    // カレンダー生成
    generateCalendars();
    
    // イベントリスナーを設定
    setupCardClickEvents();
    
    // 初期データ表示（CSV読み込み後）
    const activeDates = getActiveDates();
    const firstDate = activeDates[0];
    console.log('Setting initial table data for:', firstDate);
    updateTableFromCSV(firstDate);
    
    // 初期チェック日を設定
    lastCheckDate = new Date().toDateString();
    
    // Refresh button functionality
    document.getElementById('refresh-btn').addEventListener('click', function() {
        refreshData();
    });
    
    // Back to top button
    initBackToTop();
    
    // 定期的な日付チェック（1時間ごと）
    setInterval(checkAndUpdateDatesIfNeeded, 60 * 60 * 1000); // 1時間ごと
    
    console.log('Initialization complete with automatic FOMC date management');
});