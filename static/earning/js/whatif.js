(function () {
  'use strict';

  const MODEL_URL = '/static/earning/ml/baseline-v1.json';
  const DEBOUNCE_MS = 100;

  const SLIDER_LABELS = {
    'vix_at_event': 'VIX',
    'hy_spread_at_event': 'HY スプレッド',
    'skew_at_event': 'SKEW',
    't5yie_at_event': '5年期待インフレ',
    'rut_at_event': 'Russell 2000',
  };

  let modelPromise = null;

  function loadModel() {
    if (!modelPromise) {
      modelPromise = fetch(MODEL_URL).then(function (r) {
        if (!r.ok) throw new Error('Model fetch failed: ' + r.status);
        return r.json();
      }).then(function (model) {
        model._featureThresholdRanges = collectThresholdRanges(model);
        return model;
      });
    }
    return modelPromise;
  }

  function collectThresholdRanges(model) {
    const out = {};
    function walk(node) {
      if ('leaf_value' in node) return;
      const name = model.feature_names[node.split_feature];
      const t = node.threshold;
      if (!out[name]) out[name] = [t, t];
      else { if (t < out[name][0]) out[name][0] = t; if (t > out[name][1]) out[name][1] = t; }
      walk(node.left_child); walk(node.right_child);
    }
    const trees = model.trees || [];
    for (let i = 0; i < trees.length; i++) walk(trees[i].root);
    return out;
  }

  function widenToModelThresholds(key, minVal, maxVal, model) {
    const tr = (model._featureThresholdRanges || {})[key];
    if (!tr) return [minVal, maxVal];
    const span = (tr[1] - tr[0]) || Math.abs(tr[0]) * 0.05 || 1;
    const pad = span * 0.05;
    return [Math.min(minVal, tr[0] - pad), Math.max(maxVal, tr[1] + pad)];
  }

  function predictFromJson(features, model) {
    let total = (model.init_score || 0);
    const trees = model.trees || [];
    for (let i = 0; i < trees.length; i++) {
      const tree = trees[i];
      const leaf = walkTree(tree.root, features);
      total += leaf * (tree.shrinkage || 1.0);
    }
    return total;
  }

  function walkTree(node, features) {
    while (!('leaf_value' in node)) {
      const idx = node.split_feature;
      const threshold = node.threshold;
      const decisionType = node.decision_type || '<=';
      const defaultLeft = node.default_left !== false;
      const v = idx < features.length ? features[idx] : null;
      let goLeft;
      if (v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))) {
        goLeft = defaultLeft;
      } else if (decisionType === '<=') {
        goLeft = v <= threshold;
      } else {
        goLeft = v < threshold;
      }
      node = goLeft ? node.left_child : node.right_child;
    }
    return node.leaf_value;
  }

  function debounce(fn, ms) {
    let timer = null;
    return function () {
      const args = arguments;
      const ctx = this;
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  function formatPercent(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return '—';
    const sign = value > 0 ? '+' : '';
    return sign + value.toFixed(2) + '%';
  }

  function formatRange(value) {
    if (Math.abs(value) >= 100) return value.toFixed(0);
    if (Math.abs(value) >= 10) return value.toFixed(1);
    return value.toFixed(2);
  }

  function initCard(card, model) {
    const baselineEl = card.querySelector('[data-whatif-baseline]');
    const rangesEl = card.querySelector('[data-whatif-ranges]');
    if (!baselineEl || !rangesEl) return;

    let baseline, ranges;
    try {
      baseline = JSON.parse(baselineEl.textContent);
      ranges = JSON.parse(rangesEl.textContent);
    } catch (e) {
      return;
    }
    if (!baseline || !ranges) return;

    const featureNames = model.feature_names;
    const slidersContainer = card.querySelector('[data-whatif-sliders]');
    const currentEl = card.querySelector('[data-whatif-current]');
    const diffEl = card.querySelector('[data-whatif-diff]');
    const resetBtn = card.querySelector('[data-whatif-reset]');

    const state = Object.assign({}, baseline);
    const inputs = {};

    function probeIsResponsive(key, minVal, maxVal) {
      const lo = featureNames.map(function (n) {
        const v = n === key ? minVal : baseline[n];
        return v === null || v === undefined ? NaN : v;
      });
      const hi = featureNames.map(function (n) {
        const v = n === key ? maxVal : baseline[n];
        return v === null || v === undefined ? NaN : v;
      });
      return Math.abs(predictFromJson(hi, model) - predictFromJson(lo, model)) > 1e-6;
    }

    Object.keys(ranges).forEach(function (key) {
      const [minVal, maxVal] = widenToModelThresholds(key, ranges[key][0], ranges[key][1], model);
      if (!probeIsResponsive(key, minVal, maxVal)) return;
      const baseVal = baseline[key];
      const row = document.createElement('div');
      row.className = 'whatif-slider-row';

      const label = document.createElement('span');
      label.className = 'whatif-slider-label';
      label.textContent = SLIDER_LABELS[key] || key;

      const input = document.createElement('input');
      input.type = 'range';
      input.className = 'whatif-slider-input';
      input.min = String(minVal);
      input.max = String(maxVal);
      input.step = String((maxVal - minVal) / 100);
      input.value = String(baseVal);

      const valueEl = document.createElement('span');
      valueEl.className = 'whatif-slider-value';
      valueEl.textContent = formatRange(baseVal) + ' (b: ' + formatRange(baseVal) + ')';

      input.addEventListener('input', debounce(function () {
        const v = parseFloat(input.value);
        state[key] = v;
        valueEl.textContent = formatRange(v) + ' (b: ' + formatRange(baseVal) + ')';
        recomputeAndRender();
      }, DEBOUNCE_MS));

      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(valueEl);
      slidersContainer.appendChild(row);
      inputs[key] = { input: input, valueEl: valueEl, baseVal: baseVal };
    });

    function buildFeatureVector() {
      const out = [];
      for (let i = 0; i < featureNames.length; i++) {
        const name = featureNames[i];
        const v = state[name];
        out.push(v === null || v === undefined ? NaN : v);
      }
      return out;
    }

    function recomputeAndRender() {
      const baselineVec = featureNames.map(function (n) {
        const v = baseline[n];
        return v === null || v === undefined ? NaN : v;
      });
      const baselinePred = predictFromJson(baselineVec, model);
      const currentVec = buildFeatureVector();
      const currentPred = predictFromJson(currentVec, model);
      const diff = currentPred - baselinePred;

      currentEl.textContent = formatPercent(currentPred);
      diffEl.textContent = '(差分 ' + formatPercent(diff) + ')';
      diffEl.classList.remove('positive', 'negative');
      if (diff > 0.01) diffEl.classList.add('positive');
      else if (diff < -0.01) diffEl.classList.add('negative');
    }

    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        Object.keys(inputs).forEach(function (key) {
          const baseVal = inputs[key].baseVal;
          inputs[key].input.value = String(baseVal);
          inputs[key].valueEl.textContent = formatRange(baseVal) + ' (b: ' + formatRange(baseVal) + ')';
          state[key] = baseVal;
        });
        recomputeAndRender();
      });
    }

    recomputeAndRender();
    card.setAttribute('data-whatif-wired', '');
  }

  function init() {
    const cards = document.querySelectorAll('[data-whatif-card]:not([data-whatif-wired])');
    if (cards.length === 0) return;
    loadModel().then(function (model) {
      cards.forEach(function (card) { initCard(card, model); });
    }).catch(function (err) {
      console.warn('whatif.js: model load failed', err);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  document.addEventListener('whatif:rescan', init);
})();
