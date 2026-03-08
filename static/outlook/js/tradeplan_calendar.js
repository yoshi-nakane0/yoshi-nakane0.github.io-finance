(function () {
  var calendar = document.querySelector("[data-tradeplan-calendar]");
  var positionData = document.getElementById("tradeplan-position-data");
  var sheet = document.getElementById("tradeplan-position-sheet");
  var sheetTitle = document.getElementById("tradeplan-position-sheet-title");
  var editor = document.getElementById("tradeplan-position-editor");
  var editorText = document.getElementById("tradeplan-position-editor-text");
  var deleteButton = editor
    ? editor.querySelector("[data-tradeplan-delete]")
    : null;
  var editCloseButton = editor
    ? editor.querySelector("[data-tradeplan-edit-close]")
    : null;
  var sheetCloseButton = sheet
    ? sheet.querySelector("[data-tradeplan-sheet-close]")
    : null;
  var createButtons = sheet
    ? Array.prototype.slice.call(
        sheet.querySelectorAll("[data-tradeplan-create]")
      )
    : [];

  if (!calendar || !positionData || !sheet || !sheetTitle || !editor || !editorText) {
    return;
  }

  var dayButtons = Array.prototype.slice.call(
    calendar.querySelectorAll("[data-tradeplan-day]")
  );
  var dayLayers = {};

  dayButtons.forEach(function (button) {
    var dayDate = button.dataset.tradeplanDate || "";
    var lineLayer = button.querySelector("[data-tradeplan-line-layer]");
    if (dayDate && lineLayer) {
      dayLayers[dayDate] = lineLayer;
    }
  });

  if (!Object.keys(dayLayers).length) {
    return;
  }

  var LONG_PRESS_MS = 420;
  var MOVE_CANCEL_PX = 8;
  var DEFAULT_TRACK_ROWS = 2;
  var TRACK_HEIGHT = 10;
  var TRACK_GAP = 6;

  var state = {
    positions: normalizePositions(parseJson(positionData.textContent)),
    pendingCreateDate: "",
    editingPositionId: "",
    editingDraft: null,
    dragState: null,
    longPressTimer: null,
    longPressState: null,
    isSaving: false,
    suppressClickUntil: 0,
  };

  function parseJson(rawValue) {
    try {
      return JSON.parse(rawValue || "[]");
    } catch (error) {
      return [];
    }
  }

  function normalizePositions(positions) {
    if (!Array.isArray(positions)) {
      return [];
    }

    return positions
      .map(function (position) {
        return normalizePosition(position);
      })
      .filter(function (position) {
        return Boolean(position.id);
      })
      .sort(comparePositionOrder);
  }

  function normalizePosition(position) {
    var startDate = clampDate(
      (position && (position.start_date || position.date)) || calendar.dataset.minDate
    );
    var endDate = clampDate(
      (position && position.end_date) || startDate || calendar.dataset.minDate
    );
    var normalizedType =
      String((position && position.type) || "").toLowerCase() === "short"
        ? "short"
        : "long";

    if (compareDates(startDate, endDate) > 0) {
      var swapped = startDate;
      startDate = endDate;
      endDate = swapped;
    }

    return {
      id: String((position && position.id) || "").trim(),
      type: normalizedType,
      start_date: startDate,
      end_date: endDate,
    };
  }

  function clampDate(dateValue) {
    var normalizedDate = String(dateValue || "").slice(0, 10);
    var minimumDate = calendar.dataset.minDate || normalizedDate;
    var maximumDate = calendar.dataset.maxDate || normalizedDate;

    if (!normalizedDate) {
      return minimumDate;
    }
    if (minimumDate && compareDates(normalizedDate, minimumDate) < 0) {
      return minimumDate;
    }
    if (maximumDate && compareDates(normalizedDate, maximumDate) > 0) {
      return maximumDate;
    }
    return normalizedDate;
  }

  function compareDates(leftDate, rightDate) {
    return String(leftDate || "").localeCompare(String(rightDate || ""));
  }

  function comparePositionOrder(leftPosition, rightPosition) {
    return (
      compareDates(leftPosition.start_date, rightPosition.start_date) ||
      compareDates(leftPosition.end_date, rightPosition.end_date) ||
      leftPosition.type.localeCompare(rightPosition.type) ||
      leftPosition.id.localeCompare(rightPosition.id)
    );
  }

  function clonePosition(position) {
    return position
      ? {
          id: position.id,
          type: position.type,
          start_date: position.start_date,
          end_date: position.end_date,
        }
      : null;
  }

  function findPositionById(positionId) {
    var matchedPosition = null;
    state.positions.some(function (position) {
      if (position.id === positionId) {
        matchedPosition = position;
        return true;
      }
      return false;
    });
    return matchedPosition;
  }

  function overlapsVisibleRange(position) {
    return (
      compareDates(position.start_date, calendar.dataset.gridEnd || position.start_date) <=
        0 &&
      compareDates(position.end_date, calendar.dataset.gridStart || position.end_date) >= 0
    );
  }

  function getVisibleDate(dateValue) {
    if (compareDates(dateValue, calendar.dataset.gridStart || dateValue) < 0) {
      return calendar.dataset.gridStart;
    }
    if (compareDates(dateValue, calendar.dataset.gridEnd || dateValue) > 0) {
      return calendar.dataset.gridEnd;
    }
    return dateValue;
  }

  function getRenderPositions() {
    var sourcePositions = state.positions.map(function (position) {
      if (state.editingPositionId && state.editingDraft && position.id === state.editingPositionId) {
        return clonePosition(state.editingDraft);
      }
      return clonePosition(position);
    });

    return sourcePositions
      .filter(function (position) {
        return position && overlapsVisibleRange(position);
      })
      .sort(comparePositionOrder);
  }

  function buildAssignments() {
    var trackEnds = [];

    return getRenderPositions().map(function (position) {
      var visibleStart = getVisibleDate(position.start_date);
      var visibleEnd = getVisibleDate(position.end_date);
      var trackIndex = 0;

      while (
        trackIndex < trackEnds.length &&
        compareDates(visibleStart, trackEnds[trackIndex]) <= 0
      ) {
        trackIndex += 1;
      }

      trackEnds[trackIndex] = visibleEnd;

      return {
        position: position,
        trackIndex: trackIndex,
        visibleStart: visibleStart,
        visibleEnd: visibleEnd,
      };
    });
  }

  function forEachDateInRange(startDate, endDate, callback) {
    var cursor = new Date(startDate + "T00:00:00Z");
    var lastDate = new Date(endDate + "T00:00:00Z");

    while (cursor <= lastDate) {
      callback(cursor.toISOString().slice(0, 10));
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
  }

  function appendHandle(segment, position, boundary) {
    var handle = document.createElement("span");
    handle.className =
      "tradeplan-calendar__line-handle " +
      (boundary === "start" ? "is-start" : "is-end");
    handle.dataset.tradeplanHandle = boundary;
    handle.dataset.positionId = position.id;
    handle.setAttribute("aria-hidden", "true");
    segment.appendChild(handle);
  }

  function renderPositions() {
    var assignments = buildAssignments();
    var trackCount = assignments.reduce(function (highestTrack, assignment) {
      return Math.max(highestTrack, assignment.trackIndex + 1);
    }, 0);
    var totalTracks = Math.max(DEFAULT_TRACK_ROWS, trackCount);
    var areaHeight =
      totalTracks * TRACK_HEIGHT + (totalTracks - 1) * TRACK_GAP;

    Object.keys(dayLayers).forEach(function (dayDate) {
      dayLayers[dayDate].innerHTML = "";
    });

    calendar.style.setProperty("--tradeplan-track-rows", String(totalTracks));
    calendar.style.setProperty(
      "--tradeplan-line-area-height",
      String(Math.max(areaHeight, 26)) + "px"
    );

    assignments.forEach(function (assignment) {
      forEachDateInRange(
        assignment.visibleStart,
        assignment.visibleEnd,
        function (dayDate) {
          var lineLayer = dayLayers[dayDate];
          if (!lineLayer) {
            return;
          }

          var segment = document.createElement("span");
          segment.className =
            "tradeplan-calendar__line-segment is-" + assignment.position.type;
          segment.style.gridRow = String(assignment.trackIndex + 1);
          segment.dataset.positionId = assignment.position.id;
          segment.dataset.positionType = assignment.position.type;
          segment.dataset.tradeplanDate = dayDate;

          if (assignment.visibleStart === assignment.visibleEnd) {
            segment.classList.add("is-single");
          } else {
            if (dayDate === assignment.visibleStart) {
              segment.classList.add("is-start");
            }
            if (dayDate === assignment.visibleEnd) {
              segment.classList.add("is-end");
            }
          }

          if (assignment.position.id === state.editingPositionId) {
            segment.classList.add("is-editing");
            if (dayDate === assignment.visibleStart) {
              appendHandle(segment, assignment.position, "start");
            }
            if (dayDate === assignment.visibleEnd) {
              appendHandle(segment, assignment.position, "end");
            }
          }

          lineLayer.appendChild(segment);
        }
      );
    });

    syncEditor();
  }

  function syncEditor() {
    var draftPosition = getEditingPosition();
    if (!draftPosition) {
      editor.hidden = true;
      return;
    }

    editor.hidden = false;
    editorText.textContent =
      (draftPosition.type === "short" ? "Short" : "Long") +
      " " +
      draftPosition.start_date +
      " - " +
      draftPosition.end_date;
  }

  function openCreateSheet(dateValue) {
    closeEditor();
    state.pendingCreateDate = dateValue;
    sheetTitle.textContent = dateValue + " のポジション種別を選択";
    sheet.hidden = false;
  }

  function closeCreateSheet() {
    state.pendingCreateDate = "";
    sheet.hidden = true;
  }

  function getEditingPosition() {
    if (!state.editingPositionId) {
      return null;
    }
    if (state.editingDraft) {
      return state.editingDraft;
    }
    return clonePosition(findPositionById(state.editingPositionId));
  }

  function enterEditMode(positionId) {
    var position = findPositionById(positionId);
    if (!position) {
      closeEditor();
      return;
    }

    closeCreateSheet();
    state.editingPositionId = positionId;
    state.editingDraft = clonePosition(position);
    renderPositions();
  }

  function closeEditor() {
    state.dragState = null;
    state.editingPositionId = "";
    state.editingDraft = null;
    renderPositions();
  }

  function setPositions(positions) {
    state.positions = normalizePositions(positions);
  }

  function getCsrfToken() {
    var cookieSource = document.cookie ? document.cookie.split(";") : [];
    var csrfToken = "";

    cookieSource.some(function (cookie) {
      var normalizedCookie = cookie.trim();
      if (normalizedCookie.indexOf("csrftoken=") === 0) {
        csrfToken = decodeURIComponent(normalizedCookie.slice(10));
        return true;
      }
      return false;
    });

    return csrfToken;
  }

  function requestPositions(payload) {
    if (state.isSaving || !calendar.dataset.positionApiUrl) {
      return Promise.reject(new Error("request_blocked"));
    }

    state.isSaving = true;

    return window
      .fetch(calendar.dataset.positionApiUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify(payload),
      })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return { ok: false, error: "invalid_response" };
          })
          .then(function (data) {
            if (!response.ok || !data.ok) {
              throw new Error(data.error || "request_failed");
            }
            return data;
          });
      })
      .finally(function () {
        state.isSaving = false;
      });
  }

  function createPosition(positionType) {
    if (!state.pendingCreateDate) {
      return;
    }

    requestPositions({
      action: "create",
      type: positionType,
      start_date: state.pendingCreateDate,
      end_date: state.pendingCreateDate,
    })
      .then(function (data) {
        setPositions(data.positions || []);
        closeCreateSheet();
        if (data.position && data.position.id) {
          enterEditMode(data.position.id);
          return;
        }
        renderPositions();
      })
      .catch(function () {
        renderPositions();
      });
  }

  function saveEditedPosition() {
    var sourcePosition = findPositionById(state.editingPositionId);
    var draftPosition = getEditingPosition();

    if (!sourcePosition || !draftPosition) {
      closeEditor();
      return;
    }

    if (
      sourcePosition.type === draftPosition.type &&
      sourcePosition.start_date === draftPosition.start_date &&
      sourcePosition.end_date === draftPosition.end_date
    ) {
      renderPositions();
      return;
    }

    requestPositions({
      action: "update",
      id: draftPosition.id,
      type: draftPosition.type,
      start_date: draftPosition.start_date,
      end_date: draftPosition.end_date,
    })
      .then(function (data) {
        setPositions(data.positions || []);
        state.editingDraft = clonePosition(
          data.position || findPositionById(draftPosition.id)
        );
        renderPositions();
      })
      .catch(function () {
        state.editingDraft = clonePosition(sourcePosition);
        renderPositions();
      });
  }

  function deleteEditingPosition() {
    var draftPosition = getEditingPosition();
    if (!draftPosition) {
      return;
    }

    requestPositions({
      action: "delete",
      id: draftPosition.id,
    })
      .then(function (data) {
        setPositions(data.positions || []);
        state.editingPositionId = "";
        state.editingDraft = null;
        renderPositions();
      })
      .catch(function () {
        renderPositions();
      });
  }

  function updateDraftBoundary(boundary, dateValue) {
    var draftPosition = getEditingPosition();
    if (!draftPosition) {
      return;
    }

    var nextStart = draftPosition.start_date;
    var nextEnd = draftPosition.end_date;
    var targetDate = clampDate(dateValue);

    if (boundary === "start") {
      nextStart = targetDate;
    } else {
      nextEnd = targetDate;
    }

    if (compareDates(nextStart, nextEnd) > 0) {
      var swapped = nextStart;
      nextStart = nextEnd;
      nextEnd = swapped;
    }

    state.editingDraft = {
      id: draftPosition.id,
      type: draftPosition.type,
      start_date: nextStart,
      end_date: nextEnd,
    };
    renderPositions();
  }

  function findDateFromPoint(clientX, clientY) {
    var target = document.elementFromPoint(clientX, clientY);
    if (!target) {
      return "";
    }

    var cell = target.closest("[data-tradeplan-cell]");
    return cell ? cell.dataset.tradeplanDate || "" : "";
  }

  function beginHandleDrag(event, handle) {
    if (event.pointerType === "mouse" && event.button !== 0) {
      return;
    }

    var boundary = handle.dataset.tradeplanHandle || "";
    var positionId = handle.dataset.positionId || "";

    if (!boundary || !positionId) {
      return;
    }

    if (state.editingPositionId !== positionId) {
      enterEditMode(positionId);
    }

    state.dragState = {
      boundary: boundary,
      pointerId: event.pointerId,
      hasMoved: false,
    };
    state.suppressClickUntil = Date.now() + 600;
    clearLongPress();
    event.preventDefault();
    event.stopPropagation();
  }

  function handlePointerMove(event) {
    if (state.dragState) {
      if (state.dragState.pointerId !== event.pointerId) {
        return;
      }

      var targetDate = findDateFromPoint(event.clientX, event.clientY);
      if (!targetDate) {
        return;
      }

      state.dragState.hasMoved = true;
      updateDraftBoundary(state.dragState.boundary, targetDate);
      event.preventDefault();
      return;
    }

    if (!state.longPressTimer || !state.longPressState) {
      return;
    }

    if (
      Math.abs(event.clientX - state.longPressState.startX) > MOVE_CANCEL_PX ||
      Math.abs(event.clientY - state.longPressState.startY) > MOVE_CANCEL_PX
    ) {
      clearLongPress();
    }
  }

  function finishHandleDrag(event) {
    if (!state.dragState || state.dragState.pointerId !== event.pointerId) {
      return;
    }

    var shouldSave = state.dragState.hasMoved;
    state.dragState = null;
    if (shouldSave) {
      saveEditedPosition();
      return;
    }
    renderPositions();
  }

  function clearLongPress() {
    if (state.longPressTimer) {
      window.clearTimeout(state.longPressTimer);
    }
    state.longPressTimer = null;
    state.longPressState = null;
  }

  function scheduleLongPress(event, target) {
    var segment = target.closest(".tradeplan-calendar__line-segment");
    var dayButton = target.closest("[data-tradeplan-day]");

    if (!segment && !dayButton) {
      return;
    }

    if (segment && segment.closest("[data-tradeplan-handle]")) {
      return;
    }

    state.longPressState = {
      startX: event.clientX,
      startY: event.clientY,
      date: dayButton ? dayButton.dataset.tradeplanDate || "" : "",
      positionId: segment ? segment.dataset.positionId || "" : "",
    };

    state.longPressTimer = window.setTimeout(function () {
      var pendingState = state.longPressState;

      clearLongPress();
      state.suppressClickUntil = Date.now() + 600;

      if (!pendingState) {
        return;
      }

      if (pendingState.positionId) {
        enterEditMode(pendingState.positionId);
        return;
      }

      if (pendingState.date) {
        openCreateSheet(pendingState.date);
      }
    }, LONG_PRESS_MS);
  }

  function handleCalendarTap(event) {
    if (Date.now() < state.suppressClickUntil) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    var handle = event.target.closest("[data-tradeplan-handle]");
    if (handle) {
      event.preventDefault();
      return;
    }

    var segment = event.target.closest(".tradeplan-calendar__line-segment");
    if (segment) {
      event.preventDefault();
      enterEditMode(segment.dataset.positionId || "");
      return;
    }

    var dayButton = event.target.closest("[data-tradeplan-day]");
    if (dayButton) {
      event.preventDefault();
      openCreateSheet(dayButton.dataset.tradeplanDate || "");
    }
  }

  calendar.addEventListener("pointerdown", function (event) {
    var handle = event.target.closest("[data-tradeplan-handle]");
    if (handle) {
      beginHandleDrag(event, handle);
      return;
    }

    if (event.pointerType === "mouse" && event.button !== 0) {
      return;
    }

    clearLongPress();
    scheduleLongPress(event, event.target);
  });

  calendar.addEventListener("pointerup", clearLongPress);
  calendar.addEventListener("pointercancel", clearLongPress);
  calendar.addEventListener("pointerleave", function () {
    if (!state.dragState) {
      clearLongPress();
    }
  });
  calendar.addEventListener("click", handleCalendarTap);

  window.addEventListener("pointermove", handlePointerMove, { passive: false });
  window.addEventListener("pointerup", function (event) {
    finishHandleDrag(event);
    clearLongPress();
  });
  window.addEventListener("pointercancel", function (event) {
    finishHandleDrag(event);
    clearLongPress();
  });

  if (sheetCloseButton) {
    sheetCloseButton.addEventListener("click", closeCreateSheet);
  }

  createButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      createPosition(button.dataset.tradeplanCreate || "");
    });
  });

  if (deleteButton) {
    deleteButton.addEventListener("click", deleteEditingPosition);
  }

  if (editCloseButton) {
    editCloseButton.addEventListener("click", closeEditor);
  }

  sheet.addEventListener("click", function (event) {
    if (event.target === sheet) {
      closeCreateSheet();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }

    clearLongPress();
    closeCreateSheet();
    closeEditor();
  });

  renderPositions();
})();
