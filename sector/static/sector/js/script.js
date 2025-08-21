// target/static/target/js/script.js

document.addEventListener('DOMContentLoaded', function() {
    // Chart data will be passed from Django view
    let sectorData = [];
    let benchmarkData = [];

    // Initialize charts when page loads
    initializeCharts();
    
    // Refresh button event listener
    document.getElementById('refresh-btn').addEventListener('click', function() {
        refreshData();
    });

    function initializeCharts() {
        // Get data from Django context (will be set in template)
        if (window.chartData) {
            sectorData = window.chartData.sectors;
            benchmarkData = window.chartData.benchmarks;
            createBarChart();
        }
    }

    function createBarChart() {
        if (!sectorData || sectorData.length === 0) return;

        // Sort data by change percentage
        const sortedData = [...sectorData].sort((a, b) => a.change_pct - b.change_pct);
        
        const colors = sortedData.map(item => item.change_pct > 0 ? '#4CAF50' : '#F44336');
        const textColors = sortedData.map(item => item.change_pct > 0 ? '#2E7D32' : '#C62828');

        const trace = {
            type: 'bar',
            x: sortedData.map(item => item.change_pct),
            y: sortedData.map(item => item.sector),
            orientation: 'h',
            marker: {
                color: colors
            },
            text: sortedData.map(item => `${item.change_pct.toFixed(1)}%`),
            textposition: 'outside',
            textfont: {
                color: textColors
            },
            hovertemplate: '<b>%{y}</b><br>変化率: %{x:.2f}%<extra></extra>'
        };

        const layout = {
            title: {
                text: '📊 セクター別パフォーマンス',
                x: 0.5,
                xanchor: 'center',
                font: { size: 24, color: '#333' }
            },
            xaxis: {
                title: '変化率 (%)',
                showgrid: true,
                gridwidth: 1,
                gridcolor: '#E0E0E0',
                zeroline: true,
                zerolinecolor: '#666'
            },
            yaxis: {
                title: '',
                showgrid: false
            },
            template: 'plotly_white',
            height: 600,
            showlegend: false,
            margin: { l: 200, r: 100, t: 80, b: 60 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)'
        };

        Plotly.newPlot('bar-plot', [trace], layout, {responsive: true});
    }

    function createHeatmap() {
        if (!sectorData || sectorData.length === 0) return;

        const usData = sectorData.filter(item => item.group === 'US');
        const jpData = sectorData.filter(item => item.group === 'JP');

        if (usData.length === 0) return;

        // Create matrix for US sectors
        const usMatrix = [usData.map(item => item.change_pct)];
        const usLabels = [usData.map(item => `${item.change_pct.toFixed(1)}%`)];

        const trace = {
            type: 'heatmap',
            z: usMatrix,
            x: usData.map(item => item.sector),
            y: ['US Sectors'],
            colorscale: 'RdYlGn',
            zmid: 0,
            text: usLabels,
            texttemplate: '%{text}',
            textfont: { size: 12 },
            hovertemplate: '<b>%{x}</b><br>変化率: %{z:.2f}%<extra></extra>'
        };

        const layout = {
            title: {
                text: '🔥 セクター・ヒートマップ',
                x: 0.5,
                xanchor: 'center',
                font: { size: 24, color: '#333' }
            },
            template: 'plotly_white',
            height: 400,
            margin: { l: 100, r: 100, t: 80, b: 150 },
            xaxis: { tickangle: 45 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)'
        };

        Plotly.newPlot('heatmap-plot', [trace], layout, {responsive: true});
    }

    function refreshData() {
        const refreshBtn = document.getElementById('refresh-btn');
        const originalText = refreshBtn.textContent;
        
        // Show loading state
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<span class="spinner"></span> 更新中...';
        
        // Add loading class to main container
        document.querySelector('.container').classList.add('loading');

        // Simulate data refresh (in real implementation, this would be an AJAX call)
        fetch(window.location.href, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({ action: 'refresh' })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Update data
                if (data.chartData) {
                    window.chartData = data.chartData;
                    sectorData = data.chartData.sectors;
                    benchmarkData = data.chartData.benchmarks;
                }
                
                // Update summary metrics
                if (data.summary) {
                    updateSummaryMetrics(data.summary);
                }
                
                // Update update time
                if (data.update_time) {
                    document.getElementById('update-time').textContent = data.update_time;
                }
                
                // Recreate charts
                createBarChart();
                
                // Update sector cards (this would typically reload the page or update via DOM manipulation)
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            }
        })
        .catch(error => {
            console.error('Error refreshing data:', error);
            // For demo purposes, just reload the page
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        })
        .finally(() => {
            // Restore button state
            setTimeout(() => {
                refreshBtn.disabled = false;
                refreshBtn.textContent = originalText;
                document.querySelector('.container').classList.remove('loading');
            }, 1000);
        });
    }

    function updateSummaryMetrics(summary) {
        document.getElementById('positive-count').textContent = summary.positive_count;
        document.getElementById('negative-count').textContent = summary.negative_count;
        document.getElementById('total-count').textContent = summary.total_count;
        document.getElementById('avg-change').textContent = `${summary.avg_change.toFixed(2)}%`;
        
        const avgIcon = document.getElementById('avg-icon');
        avgIcon.textContent = summary.avg_change > 0 ? '📈' : '📉';
        
        // Update average metric card class
        const avgCard = document.querySelector('.metric-card.average');
        if (summary.avg_change > 0) {
            avgCard.className = 'metric-card positive';
        } else {
            avgCard.className = 'metric-card negative';
        }
    }

    // Utility function to get CSRF token
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

    // Animation utilities
    function animateMetricCard(element, newValue) {
        element.style.transform = 'scale(1.1)';
        setTimeout(() => {
            element.textContent = newValue;
            element.style.transform = 'scale(1)';
        }, 150);
    }

    // チャート切り替え機能を削除（ヒートマップ削除のため）

    // Responsive chart resizing
    let resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            if (document.getElementById('bar-plot')) {
                Plotly.Plots.resize('bar-plot');
            }
        }, 250);
    });

    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
});