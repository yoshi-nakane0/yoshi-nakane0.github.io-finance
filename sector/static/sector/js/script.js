// セクターページ用スクリプト
document.addEventListener('DOMContentLoaded', function() {
    let sectorData = [];
    
    // 初期化
    initializeCharts();
    
    // 更新ボタン
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', refreshData);
    }

    function initializeCharts() {
        if (window.chartData) {
            sectorData = window.chartData.sectors;
            createBarChart();
        }
    }

    function createBarChart() {
        if (!sectorData || sectorData.length === 0) return;

        const sortedData = [...sectorData].sort((a, b) => a.change_pct - b.change_pct);
        const colors = sortedData.map(item => item.change_pct > 0 ? '#4CAF50' : '#F44336');

        const trace = {
            type: 'bar',
            x: sortedData.map(item => item.change_pct),
            y: sortedData.map(item => item.sector),
            orientation: 'h',
            marker: { color: colors },
            text: sortedData.map(item => `${item.change_pct.toFixed(1)}%`),
            textposition: 'outside',
            hovertemplate: '<b>%{y}</b><br>変化率: %{x:.2f}%<extra></extra>'
        };

        const layout = {
            title: {
                text: '📊 セクター別パフォーマンス',
                x: 0.5,
                font: { size: 20, color: '#333' }
            },
            xaxis: {
                title: '変化率 (%)',
                showgrid: true,
                zeroline: true
            },
            yaxis: { title: '' },
            height: 500,
            margin: { l: 150, r: 50, t: 60, b: 40 },
            paper_bgcolor: 'rgba(0,0,0,0)'
        };

        Plotly.newPlot('bar-plot', [trace], layout, {responsive: true});
    }

    function refreshData() {
        refreshBtn.disabled = true;
        refreshBtn.textContent = '更新中...';
        
        setTimeout(() => {
            window.location.reload();
        }, 500);
    }

});