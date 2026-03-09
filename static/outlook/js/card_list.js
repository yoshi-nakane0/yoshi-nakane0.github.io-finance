(function () {
  var itemTab = document.getElementById("outlook-item-tab");
  var watchUntilField = document.getElementById("watch-until-field");
  var watchUntilInput = watchUntilField
    ? watchUntilField.querySelector('input[name="watch_until"]')
    : null;
  var cardForm = document.getElementById("outlook-card-list-form");
  var cardList = document.getElementById("outlook-card-list");
  var selectionToolbar = document.getElementById("outlook-selection-toolbar");
  var selectionCount = document.getElementById("outlook-selection-count");
  var selectionDelete = document.getElementById("outlook-selection-delete");
  var selectionCancel = document.getElementById("outlook-selection-cancel");
  var selectionInputs = document.getElementById("outlook-selection-inputs");
  var cards = cardList
    ? Array.prototype.slice.call(cardList.querySelectorAll(".js-selectable-card"))
    : [];

  function setupItemFormTabState() {
    if (!itemTab || !watchUntilField || !watchUntilInput) {
      return;
    }

    function syncItemFormTabState() {
      var isWatchTab = itemTab.value === "watch";
      watchUntilField.hidden = !isWatchTab;
      watchUntilInput.disabled = !isWatchTab;
      if (!isWatchTab) {
        watchUntilInput.value = "";
      }
    }

    itemTab.addEventListener("change", syncItemFormTabState);
    syncItemFormTabState();
  }

  function setupExpandableText() {
    document.addEventListener("click", function (event) {
      var item = event.target.closest("[data-expandable-text]");
      var hint;
      var expanded;

      if (!item) {
        return;
      }

      expanded = item.getAttribute("aria-expanded") === "true";
      hint = item.querySelector("[data-collapsible-hint]");

      item.setAttribute("aria-expanded", expanded ? "false" : "true");
      item.classList.toggle("is-expanded", !expanded);
      item.classList.toggle("is-clamped", expanded);
      if (hint) {
        hint.textContent = expanded
          ? "タップして展開"
          : "タップして折りたたむ";
      }
    });
  }

  function setupSelectionMode() {
    var selectionMode = false;
    var selectedIds = new Set();
    var pressTimer = null;
    var pressCard = null;
    var pressPoint = null;
    var longPressCard = null;

    if (
      !cardForm ||
      !cardList ||
      !selectionToolbar ||
      !selectionCount ||
      !selectionDelete ||
      !selectionCancel ||
      !selectionInputs ||
      !cards.length
    ) {
      return;
    }

    function syncSelectionInputs() {
      var fragment = document.createDocumentFragment();

      selectionInputs.textContent = "";
      Array.from(selectedIds).forEach(function (itemId) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "selected_ids";
        input.value = itemId;
        fragment.appendChild(input);
      });
      selectionInputs.appendChild(fragment);
    }

    function syncSelectionUi() {
      var selectedCountValue = selectedIds.size;

      cardForm.classList.toggle("is-selection-mode", selectionMode);
      selectionToolbar.hidden = !selectionMode;
      selectionDelete.disabled = selectedCountValue === 0;
      selectionCount.textContent = selectedCountValue + "件選択中";
      cards.forEach(function (card) {
        card.classList.toggle("is-selected", selectedIds.has(card.dataset.itemId));
      });
      syncSelectionInputs();
    }

    function enterSelectionMode(initialCard) {
      selectionMode = true;
      if (initialCard) {
        selectedIds.add(initialCard.dataset.itemId);
      }
      syncSelectionUi();
    }

    function clearSelectionMode() {
      selectionMode = false;
      selectedIds.clear();
      syncSelectionUi();
    }

    function toggleCardSelection(card) {
      var itemId = card.dataset.itemId;

      if (selectedIds.has(itemId)) {
        selectedIds.delete(itemId);
      } else {
        selectedIds.add(itemId);
      }
      syncSelectionUi();
    }

    function clearPressTimer() {
      if (pressTimer) {
        window.clearTimeout(pressTimer);
        pressTimer = null;
      }
      pressCard = null;
      pressPoint = null;
    }

    function shouldIgnorePress(event) {
      return Boolean(
        event.target.closest("button, a, input, textarea, select, label")
      );
    }

    cardList.addEventListener("pointerdown", function (event) {
      var card = event.target.closest(".js-selectable-card");

      if (!card || selectionMode || shouldIgnorePress(event)) {
        return;
      }
      if (event.pointerType === "mouse" && event.button !== 0) {
        return;
      }

      pressCard = card;
      pressPoint = { x: event.clientX, y: event.clientY };
      longPressCard = null;
      pressTimer = window.setTimeout(function () {
        longPressCard = card;
        enterSelectionMode(card);
        clearPressTimer();
      }, 420);
    });

    cardList.addEventListener("pointermove", function (event) {
      var card = event.target.closest(".js-selectable-card");

      if (!pressTimer || !pressCard || !pressPoint || card !== pressCard) {
        return;
      }
      if (
        Math.abs(event.clientX - pressPoint.x) > 10 ||
        Math.abs(event.clientY - pressPoint.y) > 10
      ) {
        clearPressTimer();
      }
    });

    cardList.addEventListener("pointerleave", function (event) {
      if (event.relatedTarget && cardList.contains(event.relatedTarget)) {
        return;
      }
      clearPressTimer();
    });

    cardList.addEventListener("click", function (event) {
      var card = event.target.closest(".js-selectable-card");

      if (!card) {
        return;
      }
      if (longPressCard === card) {
        longPressCard = null;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      if (!selectionMode || shouldIgnorePress(event)) {
        return;
      }

      event.preventDefault();
      toggleCardSelection(card);
    });

    cardList.addEventListener("keydown", function (event) {
      var card = event.target.closest(".js-selectable-card");

      if (!card || !selectionMode) {
        return;
      }
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }

      event.preventDefault();
      toggleCardSelection(card);
    });

    selectionCancel.addEventListener("click", clearSelectionMode);
    document.addEventListener("pointerup", clearPressTimer);
    document.addEventListener("pointercancel", clearPressTimer);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && selectionMode) {
        clearSelectionMode();
      }
    });
    syncSelectionUi();
  }

  setupItemFormTabState();
  setupExpandableText();
  setupSelectionMode();
})();
