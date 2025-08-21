// FedWatch Tool用JavaScript
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, setting up FedWatch Tool');
    setupDateCardClicks();
    setupRefreshButton();
    
    // 初期表示：最初のアクティブなカードのデータを表示
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
            tbody.innerHTML = `<tr><td colspan="4">${date}のデータなし</td></tr>`;
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
        tbody.innerHTML = `<tr><td colspan="4">データの読み込みエラー: ${error.message}</td></tr>`;
    }
}

function setupRefreshButton() {
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function() {
            refreshBtn.disabled = true;
            refreshBtn.textContent = '更新中...';
            setTimeout(() => window.location.reload(), 500);
        });
    }
}