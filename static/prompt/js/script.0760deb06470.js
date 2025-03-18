// script.js
document.addEventListener('DOMContentLoaded', function() {
    // コピーボタンの処理（表示されているテキストをコピーするように変更）
    document.querySelectorAll('.copy-btn').forEach(button => {
        button.addEventListener('click', function() {
            // data-prompt ではなく、表示されているテキストを取得
            const promptContent = this.closest('.card').querySelector('.prompt-content');
            const promptText = promptContent.textContent;

            navigator.clipboard.writeText(promptText).then(() => {
                const originalIcon = this.innerHTML;
                this.innerHTML = '<i class="bi bi-check-lg"></i>';
                this.classList.add('copied');

                // アニメーション効果
                const card = this.closest('.card');
                card.classList.add('copy-flash');

                setTimeout(() => {
                    this.innerHTML = originalIcon;
                    this.classList.remove('copied');
                    card.classList.remove('copy-flash');
                }, 1500);
            }).catch(err => {
                console.error('コピーに失敗しました:', err);
                alert('コピーに失敗しました');
            });
        });
    });

    // 翻訳ボタンの処理
    document.querySelectorAll('.translate-btn').forEach(button => {
        button.addEventListener('click', function() {
            const jpPrompt = this.getAttribute('data-jp-prompt');
            const enPrompt = this.getAttribute('data-en-prompt');
            const promptContent = this.closest('.card').querySelector('.prompt-content');
            const isCurrentlyJapanese = promptContent.getAttribute('data-lang') !== 'en';

            if (isCurrentlyJapanese) {
                // 日本語から英語へ
                promptContent.textContent = enPrompt;
                promptContent.setAttribute('data-lang', 'en');
                this.querySelector('i').classList.remove('bi-translate');
                this.querySelector('i').classList.add('bi-arrow-return-left');
                this.setAttribute('title', '日本語に戻す');
            } else {
                // 英語から日本語へ  元の全文の日本語に戻す
                promptContent.textContent = jpPrompt;
                promptContent.setAttribute('data-lang', 'jp');
                this.querySelector('i').classList.remove('bi-arrow-return-left');
                this.querySelector('i').classList.add('bi-translate');
                this.setAttribute('title', '英語に翻訳');
            }
            // ... (省略: カードのスタイル変更部分は同じ)
              // カード内の翻訳状態を視覚的に表示
            const card = this.closest('.card');
            if (isCurrentlyJapanese) {
                card.classList.add('translated');
            } else {
                card.classList.remove('translated');
            }
        });
    });

    // 検索機能
    const searchInput = document.querySelector('.search-input');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const searchTerm = this.value.toLowerCase().trim();
            filterCards(searchTerm, null);
        });
    }

    // フィルターボタンの処理
    document.querySelectorAll('.filter-btn').forEach(button => {
        button.addEventListener('click', function() {
            // アクティブクラスの切り替え
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            this.classList.add('active');

            const filterValue = this.getAttribute('data-filter');
            const searchTerm = document.querySelector('.search-input')?.value.toLowerCase().trim() || '';
            filterCards(searchTerm, filterValue);
        });
    });

    // カードのフィルタリング関数
    function filterCards(searchTerm, filterValue) {
        document.querySelectorAll('.prompt-card').forEach(card => {
            // テキスト検索（プロンプト内容とタイトルを検索）
            const promptContent = card.querySelector('.prompt-content').textContent.toLowerCase();
            const cardTitle = card.querySelector('h5').textContent.toLowerCase();  //h5を検索対象にする
            const textMatch = !searchTerm ||
                              promptContent.includes(searchTerm) ||
                              cardTitle.includes(searchTerm);

            // フィルター条件
            let filterMatch = true;
            if (filterValue && filterValue !== 'all') {
                // target_ai と usage の両方でフィルタリング
                if (filterValue === '★★★★★') {
                    filterMatch = card.getAttribute('data-usage') === filterValue;
                } else {
                    filterMatch = card.getAttribute('data-model') === filterValue || card.getAttribute('data-category') === filterValue;
                }
            }


            // 両方の条件に一致する場合のみ表示
            if (textMatch && filterMatch) {
                card.style.display = '';
                // アニメーション効果
                setTimeout(() => {
                    card.classList.add('visible');
                }, 10);
            } else {
                card.classList.remove('visible');
                card.style.display = 'none';
            }
        });

        // 結果がない場合のメッセージ表示
        const noResultsMsg = document.querySelector('.no-results-message');
        const visibleCards = document.querySelectorAll('.prompt-card[style="display: "]').length;

        if (visibleCards === 0) {
            if (!noResultsMsg) {
                const message = document.createElement('div');
                message.className = 'no-results-message text-center py-4 text-muted';
                message.innerHTML = '<i class="bi bi-search me-2"></i>検索結果がありません';
                document.querySelector('.prompt-cards').after(message);
            }
        } else if (noResultsMsg) {
            noResultsMsg.remove();
        }
    }

    // カードのホバーエフェクト
    document.querySelectorAll('.prompt-card').forEach(card => {
        card.addEventListener('mouseenter', function() {
            this.classList.add('card-hover');
        });
        card.addEventListener('mouseleave', function() {
            this.classList.remove('card-hover');
        });
    });

    // ページネーションの処理
    document.querySelectorAll('.pagination .page-link').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();

            // 現在のアクティブページを非アクティブに
            document.querySelector('.page-item.active').classList.remove('active');

            // クリックされたページをアクティブに
            if (!this.parentElement.classList.contains('disabled')) {
                this.parentElement.classList.add('active');

                // ここで実際のページネーション処理（APIコールなど）を行うことができます
                // サンプルとして、ページ変更時にスクロールアニメーションを追加
                window.scrollTo({
                    top: document.querySelector('.prompt-container').offsetTop - 20,
                    behavior: 'smooth'
                });
            }
        });
    });

    // 初期表示時にカードを表示アニメーション
    setTimeout(() => {
        document.querySelectorAll('.prompt-card').forEach((card, index) => {
            setTimeout(() => {
                card.classList.add('visible');
            }, index * 50); // カードが順番に表示される
        });
    }, 100);

    // レスポンシブ対応: フィルターバーのスクロール処理
    const filterBar = document.querySelector('.filter-bar');
    if (filterBar) {
        // スクロール可能なことを示すインジケーターの表示/非表示
        const checkFilterBarScroll = () => {
            if (filterBar.scrollWidth > filterBar.clientWidth) {
                filterBar.classList.add('scrollable');
            } else {
                filterBar.classList.remove('scrollable');
            }
        };

        // 初期チェックとウィンドウリサイズ時のチェック
        checkFilterBarScroll();
        window.addEventListener('resize', checkFilterBarScroll);
    }

    // クリップボードAPIが利用可能かチェック
    if (!navigator.clipboard) {
        document.querySelectorAll('.copy-btn').forEach(btn => {
            btn.title = 'お使いのブラウザではコピー機能がサポートされていません';
            btn.classList.add('disabled');
        });
    }
});

// CSSアニメーション用にカスタムスタイルを追加
(function addAnimationStyles() {
    const style = document.createElement('style');
    style.textContent = `
        .prompt-card {
            opacity: 0;
            transform: translateY(10px);
            transition: opacity 0.3s ease, transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
        }

        .prompt-card.visible {
            opacity: 1;
            transform: translateY(0);
        }

        .prompt-card.card-hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
        }

        .copied {
            background-color: #238636 !important;
            color: white !important;
            border-color: #238636 !important;
        }

        .copy-flash {
            animation: flash 0.5s;
        }

        @keyframes flash {
            0% { background-color: #1A1E29; }
            50% { background-color: rgba(35, 134, 54, 0.2); }
            100% { background-color: #1A1E29; }
        }

        .translated {
            border-left: 3px solid #1F6FEB !important;
        }

        .filter-bar.scrollable::after {
            content: '';
            position: absolute;
            right: 0;
            top: 0;
            height: 100%;
            width: 30px;
            background: linear-gradient(90deg, transparent, #161B22 80%);
            pointer-events: none;
        }

        .no-results-message {
            animation: fadeIn 0.5s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
    `;
    document.head.appendChild(style);
})();