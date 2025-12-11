// script.js
document.addEventListener('DOMContentLoaded', function() {
    // --- DOM 要素取得 ---
    const promptCardsContainer = document.querySelector('.prompt-cards');
    let allCards = Array.from(promptCardsContainer.querySelectorAll('.prompt-card')); // 初期状態のカードリスト (DOM順)
    const paginationListTop = document.getElementById('pagination-list-top');
    const paginationListBottom = document.getElementById('pagination-list-bottom');
    const itemsPerPageSelectTop = document.getElementById('items-per-page-top');
    const itemsPerPageSelectBottom = document.getElementById('items-per-page-bottom');
    const paginationControlsTop = paginationListTop ? paginationListTop.closest('.pagination-controls') : null;
    const paginationControlsBottom = paginationListBottom ? paginationListBottom.closest('.pagination-controls') : null;
    const searchInput = document.querySelector('.search-input');
    const filterButtons = document.querySelectorAll('.filter-btn');
    const initialPlaceholder = document.querySelector('.no-results-placeholder'); // 初期状態のプレースホルダー

    // --- 状態変数 ---
    let currentPage = 1;
    let itemsPerPage = 20; // 初期表示件数
    let currentFilteredCards = allCards; // 現在フィルタリング/検索されているカードリスト
    let draggedItem = null; // ドラッグ中の要素
    let placeholder = null; // ドラッグ中のプレースホルダー要素
    let noResultsMsgElement = null; // 動的に生成する「結果なし」メッセージ要素

    // --- ページネーション関連 ---

    /**
     * 指定されたカードリストの特定のページを表示する関数
     * @param {number} page 表示するページ番号
     * @param {Array<HTMLElement>} cards 表示対象のカード要素の配列
     */
    function displayPage(page, cards) {
        currentPage = page;
        // 一旦すべてのカードを非表示にする（DOM内の順序は維持）
        allCards.forEach(card => card.style.display = 'none');

        const startIndex = (page - 1) * itemsPerPage;
        const endIndex = startIndex + itemsPerPage;
        const paginatedCards = cards.slice(startIndex, endIndex);

        paginatedCards.forEach((card, index) => {
            card.style.display = ''; // 対象のカードを表示
            // アニメーション用クラスの制御
            card.classList.remove('visible', 'visible-init');
            void card.offsetWidth; // 再描画を強制
             setTimeout(() => {
                 card.classList.add('visible');
             }, index * 30); // 表示タイミングをずらしてアニメーション
        });

        // ページネーションUIの更新
        updatePaginationControlsVisibility(cards.length);
        updatePaginationUI(cards.length, paginationListTop);
        updatePaginationUI(cards.length, paginationListBottom);
        // ページ変更時にスクロール (任意)
        // scrollToTop();
    }

    /**
     * 指定されたUL要素にページネーションUIを生成する関数
     * @param {number} totalItems 表示対象のアイテム総数
     * @param {HTMLUListElement} listElement ページネーションを描画するUL要素
     */
    function setupPagination(totalItems, listElement) {
        if (!listElement) return;
        listElement.innerHTML = ''; // 既存のページネーションをクリア
        const totalPages = Math.ceil(totalItems / itemsPerPage);

        if (totalPages <= 1) {
            // ページネーションulは空のまま（表示/非表示は親コンテナで制御）
            return;
        }
        // 「前へ」ボタン
        const prevLi = createPageItem('前へ', currentPage === 1, () => {
             if (currentPage > 1) {
                displayPage(currentPage - 1, currentFilteredCards);
            }
        });
         if (currentPage === 1) prevLi.classList.add('disabled');
         listElement.appendChild(prevLi);

        // ページ番号ボタン
        for (let i = 1; i <= totalPages; i++) {
            const pageLi = createPageItem(i, i === currentPage, () => {
                if (i !== currentPage) {
                    displayPage(i, currentFilteredCards);
                }
            });
             if (i === currentPage) pageLi.classList.add('active');
             listElement.appendChild(pageLi);
        }

        // 「次へ」ボタン
         const nextLi = createPageItem('次へ', currentPage >= totalPages, () => {
             if (currentPage < totalPages) {
                 displayPage(currentPage + 1, currentFilteredCards);
             }
         });
         if (currentPage >= totalPages) nextLi.classList.add('disabled');
         listElement.appendChild(nextLi);
    }

    /**
     * ページネーションのアイテム（li要素）を生成するヘルパー関数
     * @param {string|number} text ボタンのテキスト
     * @param {boolean} isActive アクティブ状態か (現在は未使用、setupPaginationで制御)
     * @param {Function} clickHandler クリック時の処理
     * @returns {HTMLLIElement} 生成されたli要素
     */
     function createPageItem(text, isDisabled, clickHandler) { // isActive 引数は不要に
        const li = document.createElement('li');
        li.classList.add('page-item');
        // disabled クラスは setupPagination/updatePaginationUI で制御
        const link = document.createElement('a');
        link.classList.add('page-link');
        link.href = '#';
        link.textContent = text;
        link.addEventListener('click', (e) => {
            e.preventDefault();
            // disabledクラスが付いていない場合のみ実行
            if (!li.classList.contains('disabled')) {
                 clickHandler();
            }
        });
        li.appendChild(link);
        return li;
    }

    /**
     * 指定されたUL要素のページネーションUIのアクティブ/無効状態を更新する関数
     * @param {number} totalItems 表示対象のアイテム総数
     * @param {HTMLUListElement} listElement 更新対象のUL要素
     */
     function updatePaginationUI(totalItems, listElement) {
        if (!listElement) return;
        const totalPages = Math.ceil(totalItems / itemsPerPage);
        const pageItems = listElement.querySelectorAll('.page-item');

        pageItems.forEach((item) => {
            const link = item.querySelector('.page-link');
            if (!link) return; // 念のため
            const text = link.textContent;
            item.classList.remove('active', 'disabled'); // いったんクリア

            if (text === '前へ') {
                if (currentPage === 1) item.classList.add('disabled');
            } else if (text === '次へ') {
                if (currentPage >= totalPages) item.classList.add('disabled');
            } else {
                // ページ番号ボタン
                const pageNumber = parseInt(text);
                if (!isNaN(pageNumber) && pageNumber === currentPage) {
                    item.classList.add('active');
                }
            }
        });
    }

    /**
     * ページネーションコントロール全体の表示/非表示を切り替える関数
     * @param {number} totalItems 表示対象のアイテム総数
     */
    function updatePaginationControlsVisibility(totalItems) {
        const totalPages = Math.ceil(totalItems / itemsPerPage);
        // 20件を超える場合のみページネーションを表示
        const shouldShowControls = totalItems > 20;

        if (paginationControlsTop) {
            paginationControlsTop.style.display = shouldShowControls ? 'flex' : 'none';
        }
        if (paginationControlsBottom) {
            paginationControlsBottom.style.display = shouldShowControls ? 'flex' : 'none';
        }
         // ページネーションのul要素自体は20件を超える場合のみ表示
         if (paginationListTop) paginationListTop.style.display = shouldShowControls ? 'flex' : 'none';
         if (paginationListBottom) paginationListBottom.style.display = shouldShowControls ? 'flex' : 'none';
    }

    // --- 表示件数変更 ---

    /**
     * 表示件数セレクタの値が変更されたときのハンドラ
     * @param {Event} event changeイベントオブジェクト
     */
    function handleItemsPerPageChange(event) {
        const newItemsPerPage = parseInt(event.target.value, 10);
        if (isNaN(newItemsPerPage) || newItemsPerPage <= 0) {
            return; // 無効な値は無視
        }
        itemsPerPage = newItemsPerPage; // グローバル変数を更新

        // 他方のセレクタの値も同期させる
        if (event.target.id === 'items-per-page-top' && itemsPerPageSelectBottom) {
            itemsPerPageSelectBottom.value = itemsPerPage;
        } else if (event.target.id === 'items-per-page-bottom' && itemsPerPageSelectTop) {
            itemsPerPageSelectTop.value = itemsPerPage;
        }

        currentPage = 1; // 表示件数を変更したら1ページ目に戻る
        filterAndPaginate(); // フィルタリングとページネーションを再実行
    }

    // --- カード操作ボタン関連 ---

    /**
     * コピーボタンクリック時の処理
     */
    function handleCopyClick() {
        const promptText = this.getAttribute('data-prompt');
        const button = this; // thisを保存
        if (navigator.clipboard && promptText) {
            navigator.clipboard.writeText(promptText)
                .then(() => {
                    // console.log('Prompt copied!');
                    button.classList.add('copied');
                    const icon = button.querySelector('i');
                    const originalIconClass = icon.className; // 元のアイコンクラスを保存
                    icon.className = 'bi bi-check-lg'; // チェックマークに変更

                    // カード全体にフラッシュ効果
                    const card = button.closest('.prompt-card');
                    if(card) {
                        card.classList.add('copy-flash');
                        setTimeout(() => card.classList.remove('copy-flash'), 500);
                    }


                    setTimeout(() => {
                        button.classList.remove('copied');
                        icon.className = originalIconClass; // 元のアイコンに戻す
                    }, 1500); // 1.5秒後に元に戻す
                })
                .catch(err => {
                    console.error('Failed to copy: ', err);
                    // エラー表示（例: アラートやツールチップ）
                    alert('コピーに失敗しました。');
                });
        } else {
             alert('コピー機能が利用できないか、プロンプトが空です。');
        }
    }

    /**
     * 翻訳ボタンクリック時の処理
     */
    function handleTranslateClick() {
        const cardBody = this.closest('.prompt-card').querySelector('.card-body');
        const promptContent = cardBody.querySelector('.prompt-content');
        const currentLang = promptContent.getAttribute('data-lang');
        const button = this;
        const icon = button.querySelector('i');

        const jpPrompt = promptContent.getAttribute('data-full-jp');
        const enPrompt = promptContent.getAttribute('data-full-en');
        const truncatedJp = promptContent.getAttribute('data-truncated-jp');
        // 英語の短縮版が必要な場合は、ここで生成するか、事前にHTMLに含める
        // const truncatedEn = enPrompt.length > 100 ? enPrompt.substring(0, 97) + '...' : enPrompt; // 例

        const isExpanded = promptContent.classList.contains('expanded');

        if (currentLang === 'jp') {
            // 日本語 -> 英語
            promptContent.textContent = isExpanded ? enPrompt : enPrompt; // 短縮版が必要なら分岐
            promptContent.setAttribute('data-lang', 'en');
            button.title = "日本語に翻訳";
            icon.className = 'bi bi-translate text-warning'; // アイコン色変更などで状態を示す
            cardBody.closest('.prompt-card').classList.add('translated'); // カードにマーク
        } else {
            // 英語 -> 日本語
            promptContent.textContent = isExpanded ? jpPrompt : truncatedJp;
            promptContent.setAttribute('data-lang', 'jp');
            button.title = "英語に翻訳";
            icon.className = 'bi bi-translate'; // アイコンを元に戻す
            cardBody.closest('.prompt-card').classList.remove('translated');
        }
        // コピーボタンのdata-promptも更新
        const copyBtn = this.closest('.card-header').querySelector('.copy-btn');
        if (copyBtn) {
            copyBtn.setAttribute('data-prompt', promptContent.textContent);
        }
        // トグルボタンのdata属性も言語に合わせて更新（現在は不要かもしれない）
    }

    /**
     * 全文/短縮表示ボタンクリック時の処理
     */
    function handleToggleClick() {
        const cardBody = this.closest('.prompt-card').querySelector('.card-body');
        const promptContent = cardBody.querySelector('.prompt-content');
        const isExpanded = promptContent.classList.contains('expanded');
        const button = this;
        const icon = button.querySelector('i');

        const currentLang = promptContent.getAttribute('data-lang');
        const fullPrompt = currentLang === 'jp' ? promptContent.getAttribute('data-full-jp') : promptContent.getAttribute('data-full-en');
        const truncatedPrompt = currentLang === 'jp' ? promptContent.getAttribute('data-truncated-jp') : promptContent.getAttribute('data-full-en'); // 英語は短縮しない例

        if (isExpanded) {
            // 短縮表示へ
            promptContent.textContent = truncatedPrompt;
            promptContent.classList.remove('expanded');
            button.title = "全文表示";
            icon.className = 'bi bi-arrows-expand';
        } else {
            // 全文表示へ
            promptContent.textContent = fullPrompt;
            promptContent.classList.add('expanded');
            button.title = "短縮表示";
            icon.className = 'bi bi-arrows-collapse';
        }
        // コピーボタンのdata-promptも更新
        const copyBtn = this.closest('.card-header').querySelector('.copy-btn');
        if (copyBtn) {
             copyBtn.setAttribute('data-prompt', promptContent.textContent);
        }
    }

    /**
     * カード操作ボタンにイベントリスナーを設定する
     */
    function setupButtonListeners() {
        // 既存リスナー削除（重複防止）
        document.querySelectorAll('.copy-btn').forEach(button => {
             button.removeEventListener('click', handleCopyClick);
             button.addEventListener('click', handleCopyClick);
        });
        document.querySelectorAll('.translate-btn').forEach(button => {
             button.removeEventListener('click', handleTranslateClick);
             button.addEventListener('click', handleTranslateClick);
        });
        document.querySelectorAll('.toggle-btn').forEach(button => {
            button.removeEventListener('click', handleToggleClick);
            button.addEventListener('click', handleToggleClick);
        });
    }

    // --- 検索とフィルター ---

    /**
     * カードのフィルタリングとページネーションの更新を行う関数
     */
    function filterAndPaginate() {
        // フィルタリング前に現在のDOM順序を allCards に反映
        allCards = Array.from(promptCardsContainer.querySelectorAll('.prompt-card'));

        const searchTerm = searchInput.value.toLowerCase().trim();
        const filterValue = document.querySelector('.filter-btn.active')?.getAttribute('data-filter') || 'all';

        currentFilteredCards = allCards.filter(card => {
            const promptContentElement = card.querySelector('.prompt-content');
            // カードが表示されていない場合でも属性は取得できるはず
            const fullJpText = card.querySelector('[data-full-jp]')?.getAttribute('data-full-jp').toLowerCase() || '';
            const fullEnText = card.querySelector('[data-full-en]')?.getAttribute('data-full-en').toLowerCase() || '';
            const cardTitle = card.querySelector('h5')?.textContent.toLowerCase() || '';

            // テキスト検索（タイトル、日本語全文、英語全文）
            const textMatch = !searchTerm ||
                              fullJpText.includes(searchTerm) ||
                              fullEnText.includes(searchTerm) ||
                              cardTitle.includes(searchTerm);

            // AIモデルフィルター
            let filterMatch = true;
            if (filterValue && filterValue !== 'all') {
                filterMatch = card.getAttribute('data-model') === filterValue;
            }
            // 将来的なフィルター追加箇所 (例: カテゴリ、用途)
            // const categoryMatch = ...;
            // const usageMatch = ...;

            return textMatch && filterMatch /* && categoryMatch && usageMatch */;
        });

        handleNoResultsMessage(currentFilteredCards.length, searchTerm, filterValue);

        // ページネーション設定と表示（currentPageは handleItemsPerPageChange でリセットされる場合がある）
        updatePaginationControlsVisibility(currentFilteredCards.length);
        setupPagination(currentFilteredCards.length, paginationListTop);
        setupPagination(currentFilteredCards.length, paginationListBottom);
        displayPage(currentPage, currentFilteredCards); // 現在のページを表示
        setupDragAndDrop(); // フィルタリング後もD&Dリスナーを再設定
    }

     /**
      * 結果がない場合のメッセージ表示/非表示を制御する関数
      * @param {number} visibleCount 表示されるカード数
      * @param {string} searchTerm 現在の検索語
      * @param {string} filterValue 現在のフィルター値
      */
     function handleNoResultsMessage(visibleCount, searchTerm, filterValue) {
         // 動的メッセージ要素がなければ作成
         if (!noResultsMsgElement) {
             noResultsMsgElement = document.createElement('div');
             noResultsMsgElement.className = 'no-results-message text-center py-4 text-muted'; // CSSクラス適用
             noResultsMsgElement.style.display = 'none'; // 初期状態は非表示
             // コンテナの後ろに追加 (placeholderより後が良いかも)
             if (initialPlaceholder && initialPlaceholder.parentNode) {
                initialPlaceholder.parentNode.insertBefore(noResultsMsgElement, initialPlaceholder.nextSibling);
             } else {
                promptCardsContainer.after(noResultsMsgElement);
             }
         }

         // 初期プレースホルダーは基本的に隠す
         if (initialPlaceholder) initialPlaceholder.style.display = 'none';

         if (visibleCount === 0) {
             let message = '';
             if (searchTerm) { // 検索結果がない場合
                 message = `<i class="bi bi-search me-2"></i>「${escapeHtml(searchTerm)}」に一致するプロンプトは見つかりませんでした。`;
             } else if (filterValue !== 'all') { // フィルターで結果がない場合
                 message = `<i class="bi bi-funnel me-2"></i>選択されたフィルターに該当するプロンプトはありません。`;
             } else { // データ自体がない場合 (初期状態 or 全削除後)
                 // このケースは initialPlaceholder を使うべきかもしれない
                 // noResultsMsgElement.style.display = 'none';
                 // if (initialPlaceholder) initialPlaceholder.style.display = '';
                 message = `<i class="bi bi-info-circle me-2"></i>表示できるプロンプトがありません。`;
             }
              if (message) {
                 noResultsMsgElement.innerHTML = message;
                 noResultsMsgElement.style.display = ''; // メッセージ表示
              } else {
                  noResultsMsgElement.style.display = 'none'; // メッセージ非表示
              }
         } else { // 結果がある場合
             noResultsMsgElement.style.display = 'none'; // メッセージ非表示
         }
     }
    // --- ドラッグ＆ドロップ機能 ---

    /**
     * ドラッグ＆ドロップ関連のイベントリスナーを設定/再設定する
     */
    function setupDragAndDrop() {
        const cards = promptCardsContainer.querySelectorAll('.prompt-card');
        cards.forEach(card => {
            // 既存のリスナーを削除（重複登録防止）
            card.removeEventListener('dragstart', handleDragStart);
            card.removeEventListener('dragend', handleDragEnd);
            const handle = card.querySelector('[data-drag-handle]');
            if(handle){
                handle.removeEventListener('mousedown', handleMouseDown);
                handle.removeEventListener('mouseup', handleMouseUp);
                // mousedownで draggable=true にする
                handle.addEventListener('mousedown', handleMouseDown);
                // mouseupで draggable=false に戻す
                handle.addEventListener('mouseup', handleMouseUp);
            }
            // カード自体にドラッグイベントを設定
            card.addEventListener('dragstart', handleDragStart);
            card.addEventListener('dragend', handleDragEnd);
        });

        // コンテナのイベントリスナーも再設定（念のため毎回設定）
        promptCardsContainer.removeEventListener('dragover', handleDragOver);
        promptCardsContainer.removeEventListener('drop', handleDrop);
        promptCardsContainer.removeEventListener('dragenter', handleDragEnter);
        promptCardsContainer.removeEventListener('dragleave', handleDragLeave);

        promptCardsContainer.addEventListener('dragover', handleDragOver);
        promptCardsContainer.addEventListener('drop', handleDrop);
        // オプショナル: ドラッグオーバー時の視覚的フィードバック用
        promptCardsContainer.addEventListener('dragenter', handleDragEnter);
        promptCardsContainer.addEventListener('dragleave', handleDragLeave);
    }

    /**
     * ドラッグハンドルの mousedown イベントハンドラ
     * カードの draggable 属性を true にする
     */
    function handleMouseDown(e) {
        // 左クリック以外は無視
        if (e.button !== 0) return;
        const card = e.target.closest('.prompt-card');
        if (card) {
            card.setAttribute('draggable', 'true');
        }
    }

    /**
     * ドラッグハンドルの mouseup イベントハンドラ
     * カードの draggable 属性を false に戻す
     */
    function handleMouseUp(e) {
        const card = e.target.closest('.prompt-card');
        if (card) {
             // 少し遅延させてfalseにする（dragendより先に実行される場合があるため）
             setTimeout(() => card.setAttribute('draggable', 'false'), 0);
        }
        // ドラッグ中でない場合（クリックだけの場合）も draggable を false に戻す
        if (draggedItem) {
             setTimeout(() => draggedItem.setAttribute('draggable', 'false'), 0);
        }
    }

    /**
     * dragstart イベントハンドラ
     */
    function handleDragStart(e) {
        // e.dataTransfer.setData('text/plain', this.id); // 必要であれば
        e.dataTransfer.effectAllowed = 'move';
        draggedItem = this;
        createPlaceholder(); // プレースホルダー作成
        // 少し遅延させてスタイル適用（ドラッグゴーストに影響しないように）
        setTimeout(() => {
            if (draggedItem) { // nullチェック追加
                draggedItem.classList.add('dragging');
                // プレースホルダーの高さをドラッグ要素に合わせる
                if (placeholder) {
                    placeholder.style.height = `${draggedItem.offsetHeight}px`;
                }
            }
        }, 0);
    }

    /**
     * dragend イベントハンドラ
     */
    function handleDragEnd(e) {
        if (!draggedItem) return;
        draggedItem.classList.remove('dragging');
        // ドラッグ終了時に draggable を false に戻す
        draggedItem.setAttribute('draggable', 'false');
        draggedItem = null;
        removePlaceholder(); // プレースホルダー削除
    }

    /**
     * dragover イベントハンドラ (コンテナ上で発生)
     */
    function handleDragOver(e) {
        e.preventDefault(); // ドロップを許可
        e.dataTransfer.dropEffect = 'move';
        if (!draggedItem || !placeholder) return;

        const container = promptCardsContainer;
        // マウスY座標に基づいて挿入位置を決定
        const afterElement = getDragAfterElement(container, e.clientY);

        // プレースホルダーを適切な位置に移動
        if (afterElement == null) {
            // 最後尾に追加 (ただしプレースホルダーが最後尾でなければ)
            if (container.lastElementChild !== placeholder) {
                container.appendChild(placeholder);
            }
        } else {
            // afterElement の前に挿入 (ただしプレースホルダーがその位置でなければ)
            if (afterElement.previousElementSibling !== placeholder) {
                container.insertBefore(placeholder, afterElement);
            }
        }
    }

    /**
     * drop イベントハンドラ (コンテナ上で発生)
     */
    function handleDrop(e) {
        e.preventDefault();
        if (!draggedItem || !placeholder || !placeholder.parentNode) return;

        const container = promptCardsContainer;
        // プレースホルダーの位置にドラッグ要素を挿入
        container.insertBefore(draggedItem, placeholder);

        removePlaceholder(); // プレースホルダー削除

        // --- DOM変更後の配列更新と再描画 ---
        // 1. DOMの順序を allCards 配列に反映
        allCards = Array.from(promptCardsContainer.querySelectorAll('.prompt-card'));

        // 2. 現在のフィルター/検索条件で currentFilteredCards を再生成
        const searchTerm = searchInput.value.toLowerCase().trim();
        const filterValue = document.querySelector('.filter-btn.active')?.getAttribute('data-filter') || 'all';
        currentFilteredCards = allCards.filter(card => {
            const promptContentElement = card.querySelector('.prompt-content');
            const fullJpText = card.querySelector('[data-full-jp]')?.getAttribute('data-full-jp').toLowerCase() || '';
            const fullEnText = card.querySelector('[data-full-en]')?.getAttribute('data-full-en').toLowerCase() || '';
            const cardTitle = card.querySelector('h5')?.textContent.toLowerCase() || '';
            const textMatch = !searchTerm || fullJpText.includes(searchTerm) || fullEnText.includes(searchTerm) || cardTitle.includes(searchTerm);
            let filterMatch = true;
            if (filterValue && filterValue !== 'all') {
                filterMatch = card.getAttribute('data-model') === filterValue;
            }
            return textMatch && filterMatch;
        });

        // 3. ページネーションUI更新と現在のページの再表示
        // D&Dは同じページ内での操作と仮定し、currentPageは維持する
        updatePaginationControlsVisibility(currentFilteredCards.length);
        setupPagination(currentFilteredCards.length, paginationListTop);
        setupPagination(currentFilteredCards.length, paginationListBottom);
        // displayPageを呼ぶとアニメーションが再実行される可能性があるので、
        // DOMは既に正しい順序になっているため、表示状態のみ調整する
        const startIndex = (currentPage - 1) * itemsPerPage;
        const endIndex = startIndex + itemsPerPage;
        const paginatedCards = currentFilteredCards.slice(startIndex, endIndex);
        allCards.forEach(card => {
             // 表示すべきカードかどうかで display を設定
             card.style.display = paginatedCards.includes(card) ? '' : 'none';
             // D&D後に visible クラスが外れている可能性があるので再付与 (アニメーションなし)
             if (paginatedCards.includes(card)) {
                 card.classList.add('visible');
             } else {
                 card.classList.remove('visible');
             }
        });
        // ページネーションボタンの状態更新
        updatePaginationUI(currentFilteredCards.length, paginationListTop);
        updatePaginationUI(currentFilteredCards.length, paginationListBottom);


        // D&Dイベントリスナーを再設定 (DOM構造変更後に必要)
        setupDragAndDrop();
        // ボタンリスナーも念のため再設定 (DOM要素自体は変わらないが安全のため)
        setupButtonListeners();
    }

    /**
     * dragenter イベントハンドラ (オプショナル)
     */
    function handleDragEnter(e) {
        e.preventDefault(); // dragover 同様 preventDefault が必要
        // 例: コンテナにボーダーを表示するなど
        // promptCardsContainer.classList.add('drag-over-container');
    }

    /**
     * dragleave イベントハンドラ (オプショナル)
     */
    function handleDragLeave(e) {
        // 例: コンテナのボーダーを元に戻す
        // promptCardsContainer.classList.remove('drag-over-container');
    }

    /**
     * プレースホルダーを作成して初期位置に挿入する
     */
    function createPlaceholder() {
        if (!placeholder) {
            placeholder = document.createElement('div');
            placeholder.classList.add('drag-placeholder');
            // 初期位置はドラッグ要素の直後が良い場合が多い
            if (draggedItem && draggedItem.parentNode) {
                 // draggedItem.insertAdjacentElement('afterend', placeholder);
                 // dragover で位置が決まるので、ここでは生成のみでも良い
            }
        }
    }
    /**
     * プレースホルダーを削除する
     */
    function removePlaceholder() {
        if (placeholder && placeholder.parentNode) {
            placeholder.parentNode.removeChild(placeholder);
        }
        placeholder = null;
    }

    /**
     * ドラッグ中のY座標に基づいて、要素をどの要素の前に挿入すべきかを決定する
     * @param {HTMLElement} container カードのコンテナ要素
     * @param {number} y ドラッグ中のマウスのY座標 (clientY)
     * @returns {HTMLElement|null} 挿入先の直前の要素。末尾の場合は null
     */
    function getDragAfterElement(container, y) {
        // コンテナ内のドラッグ可能要素（現在ドラッグ中の要素とプレースホルダーを除く）を取得
        const draggableElements = [...container.querySelectorAll('.prompt-card:not(.dragging):not(.drag-placeholder)')];

        return draggableElements.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            // 要素の中間点より上か下かで判断
            const offset = y - box.top - box.height / 2;
            // console.log(`Offset for ${child.querySelector('h5')?.textContent}: ${offset}`);
            // offset < 0 はマウスが要素の中間点より上にあることを意味する
            // かつ、これまで見つけた要素よりマウスに近い（offsetが大きい）場合
            if (offset < 0 && offset > closest.offset) {
                return { offset: offset, element: child };
            } else {
                return closest;
            }
        }, { offset: Number.NEGATIVE_INFINITY }).element; // 初期値は負の無限大
    }

    // --- その他ユーティリティ ---

    /**
     * HTMLエスケープ関数（XSS防止）
     */
    function escapeHtml(unsafe) {
        return unsafe
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    /**
     * ページ上部にスムーズスクロールする
     */
    function scrollToTop() {
        const containerTop = document.querySelector('.prompt-container')?.offsetTop || 0;
        window.scrollTo({
            top: containerTop - 20, // 少し上に余裕を持たせる
            behavior: 'smooth'
        });
    }

    /**
     * フィルターバーが横スクロール可能かチェックし、クラスを付与する
     */
    function checkFilterBarScroll() {
        const filterBar = document.querySelector('.filter-bar');
        if (filterBar) {
            // 要素が表示されてから計算する必要がある
            requestAnimationFrame(() => {
                 if (filterBar.scrollWidth > filterBar.clientWidth) {
                     filterBar.classList.add('scrollable');
                 } else {
                     filterBar.classList.remove('scrollable');
                 }
            });
        }
    }


    // --- 初期化処理 ---
    function initialize() {
        // itemsPerPage の初期値をセレクタに設定
        if (itemsPerPageSelectTop) itemsPerPageSelectTop.value = itemsPerPage;
        if (itemsPerPageSelectBottom) itemsPerPageSelectBottom.value = itemsPerPage;

        // allCards は最初にDOMから取得済み
        currentFilteredCards = allCards; // 初期フィルター済みリスト

        handleNoResultsMessage(currentFilteredCards.length, '', 'all'); // 初期メッセージ処理
        updatePaginationControlsVisibility(currentFilteredCards.length); // ページネーション表示制御
        setupPagination(currentFilteredCards.length, paginationListTop); // 上部ページネーション設定
        setupPagination(currentFilteredCards.length, paginationListBottom); // 下部ページネーション設定
        displayPage(1, currentFilteredCards); // 最初のページを表示
        setupButtonListeners(); // カード操作ボタンのリスナーを設定
        setupDragAndDrop();     // ドラッグ＆ドロップのリスナーを設定

        // 検索入力イベント
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                currentPage = 1; // 検索時は1ページ目に戻る
                filterAndPaginate();
            });
        }

        // フィルターボタンクリックイベント
        filterButtons.forEach(button => {
            button.addEventListener('click', function() {
                filterButtons.forEach(btn => btn.classList.remove('active'));
                this.classList.add('active');
                currentPage = 1; // フィルター変更時は1ページ目に戻る
                filterAndPaginate();
            });
        });

        // 表示件数選択イベント
        if (itemsPerPageSelectTop) {
            itemsPerPageSelectTop.addEventListener('change', handleItemsPerPageChange);
        }
        if (itemsPerPageSelectBottom) {
            itemsPerPageSelectBottom.addEventListener('change', handleItemsPerPageChange);
        }


        // カードホバー効果
        // D&D実装により、mouseenter/mouseleaveでのクラス追加は不要かも (CSSで :hover を使う)
        // ただし、JSで制御したい場合は以下のように記述
        allCards.forEach(card => {
            card.addEventListener('mouseenter', function() {
                 if (!this.classList.contains('dragging')) { // ドラッグ中でなければ
                    // this.classList.add('card-hover'); // CSS側で :not(.dragging):hover で対応
                 }
             });
            card.addEventListener('mouseleave', function() {
                 // this.classList.remove('card-hover');
             });
        });

        // フィルターバースクロールチェック
        checkFilterBarScroll();
        window.addEventListener('resize', checkFilterBarScroll); // リサイズ時にもチェック
        // 初期表示時に要素が確定してから再チェックする場合
        // setTimeout(checkFilterBarScroll, 100);


        // クリップボードAPI サポートチェック
        if (!navigator.clipboard) {
             console.warn('Clipboard API not supported. Copy functionality may not work.');
             // 必要であればコピーボタンを無効化するなどの処理
             // document.querySelectorAll('.copy-btn').forEach(btn => btn.disabled = true);
         }
    }

    // 初期化実行
    initialize();

}); // End DOMContentLoaded