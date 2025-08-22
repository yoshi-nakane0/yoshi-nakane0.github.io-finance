// FedWatch Tool用JavaScript
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, setting up FedWatch Tool');
    setupDateCardClicks();
    setupRefreshButton();
    
    // サーバーからのデータとlocalStorageを同期
    syncDataWithServer();
    
    // 保存された状態を復元
    restorePageState();
    
    // 初期表示：アクティブなカードのデータを表示
    const activeCard = document.querySelector('.date-card.active');
    if (activeCard) {
        const initialDate = activeCard.getAttribute('data-date');
        console.log('Initial active date:', initialDate);
        updateTable(initialDate);
    }
});

function setupDateCardClicks() {
    const cards = document.querySelectorAll('.date-card');
    console.log('Setting up click listeners for', cards.length, 'cards');
    
    cards.forEach((card, index) => {
        const date = card.getAttribute('data-date');
        console.log(`Card ${index}: ${date}`);
        
        card.addEventListener('click', function() {
            const selectedDate = this.getAttribute('data-date');
            console.log('Card clicked:', selectedDate);
            
            // アクティブ状態更新
            document.querySelectorAll('.date-card').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            
            // 状態を保存
            savePageState(selectedDate);
            
            // テーブル更新
            updateTable(selectedDate);
        });
    });
}

function updateTable(date) {
    console.log('updateTable called for date:', date);
    
    const tbody = document.getElementById('fed-probabilities');
    const dataElement = document.getElementById('django-data');
    
    console.log('tbody found:', !!tbody);
    console.log('dataElement found:', !!dataElement);
    
    if (!tbody || !dataElement) {
        console.error('Missing required elements');
        return;
    }
    
    tbody.innerHTML = '';
    
    try {
        const rawData = dataElement.textContent;
        console.log('Raw data length:', rawData ? rawData.length : 'null');
        
        const fedData = JSON.parse(rawData);
        console.log('Available dates:', Object.keys(fedData));
        
        const probabilities = fedData[date];
        console.log(`Data for ${date}:`, probabilities);
        
        if (!probabilities) {
            console.warn(`No data found for date: ${date}`);
            return;
        }
        
        console.log(`Creating ${probabilities.length} rows for ${date}`);
        
        probabilities.forEach((prob, index) => {
            console.log(`Row ${index}:`, prob);
            const tr = document.createElement('tr');
            tr.className = `prob-row ${prob.type}`;
            
            tr.innerHTML = `
                <td class="rate-cell">${prob.range}</td>
                <td class="prob-cell">${prob.current}</td>
                <td class="prob-cell">${prob.oneDay}</td>
                <td class="prob-cell">${prob.oneWeek}</td>
            `;
            
            tbody.appendChild(tr);
        });
        
        console.log(`Successfully updated table with ${probabilities.length} rows`);
    } catch (error) {
        console.error('JSON parse error:', error);
        console.error('Error details:', error.message);
    }
}

function setupRefreshButton() {
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function() {
            refreshFedData();
        });
    }
}

function refreshFedData() {
    const refreshBtn = document.getElementById('refresh-btn');
    
    // ボタンの状態を更新中に変更
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = '更新中...';
    }
    
    // POSTリクエストでデータ更新後、ページをリロード（sectorページと同じ方法）
    fetch(window.location.href, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
            action: 'refresh'
        })
    })
    .then(response => response.json())
    .then(data => {
        console.log('Refresh response:', data);
        
        if (data.success) {
            // 成功時はページをリロード
            setTimeout(() => {
                window.location.reload();
            }, 500);
        } else {
            console.error('Refresh failed:', data.error);
            showNotification('データの更新に失敗しました: ' + data.error, 'error');
            // ボタンの状態を元に戻す
            if (refreshBtn) {
                refreshBtn.disabled = false;
                refreshBtn.textContent = '🔄 更新';
            }
        }
    })
    .catch(error => {
        console.error('Refresh request failed:', error);
        showNotification('ネットワークエラーが発生しました', 'error');
        // ボタンの状態を元に戻す
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '🔄 更新';
        }
    });
}

function updateAllTables(fedData) {
    // グローバルデータを更新
    const dataElement = document.getElementById('django-data');
    if (dataElement) {
        dataElement.textContent = JSON.stringify(fedData);
    }
    
    // 日付カードを更新
    updateDateCards(fedData);
    
    console.log('Updated global data with', Object.keys(fedData).length, 'meetings');
}

function updateDateCards(fedData) {
    const meetingDatesContainer = document.getElementById('fed-meeting-dates');
    if (!meetingDatesContainer) {
        console.error('Meeting dates container not found');
        return;
    }
    
    // 既存のカードをクリア
    meetingDatesContainer.innerHTML = '';
    
    const dates = Object.keys(fedData);
    console.log('Creating date cards for:', dates);
    
    // 最大9個の日付カードを作成
    dates.slice(0, 9).forEach((date, index) => {
        const dateCard = document.createElement('div');
        dateCard.className = `date-card ${index === 0 ? 'active' : ''}`;
        dateCard.setAttribute('data-date', date);
        
        // 日付を表示用にフォーマット
        const displayDate = formatDateForDisplay(date);
        
        dateCard.innerHTML = `
            <div class="date-icon">📅</div>
            <h4>${displayDate}</h4>
            <p>🏛️ FOMC会合</p>
        `;
        
        // クリックイベントを追加
        dateCard.addEventListener('click', function() {
            console.log('Date card clicked:', date);
            
            // アクティブ状態を更新
            document.querySelectorAll('.date-card').forEach(card => {
                card.classList.remove('active');
            });
            this.classList.add('active');
            
            // 状態を保存
            savePageState(date);
            
            // テーブルを更新
            updateTable(date);
        });
        
        meetingDatesContainer.appendChild(dateCard);
    });
    
    // 最初の日付のデータを表示
    if (dates.length > 0) {
        updateTable(dates[0]);
    }
}

function formatDateForDisplay(dateStr) {
    // "2025-09-17" -> "Sep 17, 2025" のような表示形式に変換
    try {
        const date = new Date(dateStr + 'T00:00:00');
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const month = months[date.getMonth()];
        const day = date.getDate();
        const year = date.getFullYear();
        return `${month} ${day}, ${year}`;
    } catch (e) {
        console.warn('Date formatting failed for:', dateStr);
        return dateStr;
    }
}

function getCsrfToken() {
    // CSRFトークンを取得（Djangoテンプレートから）
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfToken) {
        return csrfToken.value;
    }
    
    // Cookieからも試行
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        const [name, value] = cookie.trim().split('=');
        if (name === 'csrftoken') {
            return value;
        }
    }
    
    return '';
}

function showNotification(message, type = 'info') {
    // 通知を表示する関数
    const notification = document.createElement('div');
    notification.className = `alert alert-${type === 'success' ? 'success' : 'danger'} alert-dismissible fade show`;
    notification.style.position = 'fixed';
    notification.style.top = '20px';
    notification.style.right = '20px';
    notification.style.zIndex = '9999';
    notification.style.maxWidth = '400px';
    
    notification.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    // 5秒後に自動削除
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 5000);
}

// データ同期機能
function syncDataWithServer() {
    try {
        const serverDataElement = document.getElementById('django-data');
        const serverUpdateTimeElement = document.getElementById('update-time-data');
        
        if (serverDataElement && serverUpdateTimeElement) {
            const serverData = JSON.parse(serverDataElement.textContent || '{}');
            const serverUpdateTime = serverUpdateTimeElement.getAttribute('data-update-time');
            
            if (Object.keys(serverData).length > 0) {
                console.log('Syncing server data to localStorage');
                saveFedDataToLocalStorage(serverData, serverUpdateTime);
            } else {
                // サーバーにデータがない場合、localStorageから復元を試行
                const localData = loadFedDataFromLocalStorage();
                if (localData && localData.fed_data && Object.keys(localData.fed_data).length > 0) {
                    console.log('No server data, using localStorage data');
                    updateAllTables(localData.fed_data);
                    
                    const updateTimeElement = document.querySelector('.update-time');
                    if (updateTimeElement && localData.update_time) {
                        updateTimeElement.innerHTML = `⏰ 最終更新: <span id="update-time">${localData.update_time}</span> (JST)`;
                    }
                }
            }
        }
    } catch (error) {
        console.warn('Failed to sync data with server:', error);
    }
}

function saveFedDataToLocalStorage(fedData, updateTime) {
    const dataToSave = {
        fed_data: fedData,
        update_time: updateTime,
        timestamp: Date.now()
    };
    
    try {
        localStorage.setItem('fedwatch_data', JSON.stringify(dataToSave));
        console.log('Fed data saved to localStorage:', Object.keys(fedData).length, 'meetings');
    } catch (error) {
        console.warn('Failed to save fed data to localStorage:', error);
    }
}

function loadFedDataFromLocalStorage() {
    try {
        const savedData = localStorage.getItem('fedwatch_data');
        if (!savedData) {
            return null;
        }
        
        const data = JSON.parse(savedData);
        
        // 24時間以内のデータのみ使用
        const hoursSinceUpdate = (Date.now() - data.timestamp) / (1000 * 60 * 60);
        if (hoursSinceUpdate > 24) {
            console.log('Stored fed data too old, clearing...');
            localStorage.removeItem('fedwatch_data');
            return null;
        }
        
        return data;
    } catch (error) {
        console.warn('Failed to load fed data from localStorage:', error);
        localStorage.removeItem('fedwatch_data');
        return null;
    }
}

// 状態保存・復元機能
function savePageState(selectedDate) {
    const state = {
        selectedDate: selectedDate,
        timestamp: Date.now()
    };
    
    try {
        localStorage.setItem('fedwatch_state', JSON.stringify(state));
        console.log('Page state saved:', state);
    } catch (error) {
        console.warn('Failed to save page state:', error);
    }
}

function restorePageState() {
    try {
        const savedState = localStorage.getItem('fedwatch_state');
        if (!savedState) {
            console.log('No saved state found');
            return;
        }
        
        const state = JSON.parse(savedState);
        console.log('Restoring page state:', state);
        
        // 24時間以内の状態のみ復元
        const hoursSinceUpdate = (Date.now() - state.timestamp) / (1000 * 60 * 60);
        if (hoursSinceUpdate > 24) {
            console.log('Saved state too old, clearing...');
            localStorage.removeItem('fedwatch_state');
            return;
        }
        
        // 保存された日付のカードがあるかチェック
        const targetCard = document.querySelector(`[data-date="${state.selectedDate}"]`);
        if (targetCard) {
            // すべてのカードからactiveクラスを除去
            document.querySelectorAll('.date-card').forEach(card => {
                card.classList.remove('active');
            });
            
            // 保存された日付のカードをアクティブに
            targetCard.classList.add('active');
            console.log('Restored active date card:', state.selectedDate);
        } else {
            console.log('Saved date card not found:', state.selectedDate);
        }
        
    } catch (error) {
        console.warn('Failed to restore page state:', error);
        localStorage.removeItem('fedwatch_state');
    }
}