/* 王艳霞中医门诊 · 页面交互（无外部依赖） */
(function () {
  "use strict";

  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function formatNumber(n) {
    return String(Math.round(n * 100) / 100);
  }

  /* ---------- 删除等危险操作二次确认 ---------- */
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  /* ---------- 快选标签：点击填入，再点取消 ---------- */
  function segments(value) {
    return value.split(/[，,、；;\s]+/).filter(Boolean);
  }

  function chipActive(input, text, joiner) {
    var value = input.value;
    return joiner ? segments(value).indexOf(text) >= 0 : value.indexOf(text) >= 0;
  }

  function refreshChips(input) {
    document.querySelectorAll('.chip[data-target="' + input.id + '"]').forEach(function (chip) {
      chip.classList.toggle("on", chipActive(input, chip.textContent, chip.dataset.joiner));
    });
  }

  document.querySelectorAll(".chip[data-target]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var input = document.getElementById(chip.dataset.target);
      var text = chip.textContent;
      var joiner = chip.dataset.joiner;
      var value = input.value.trim();
      if (joiner) {
        var parts = segments(value);
        var i = parts.indexOf(text);
        if (i >= 0) {
          parts.splice(i, 1);
          input.value = parts.join(joiner);
        } else {
          input.value = value ? value.replace(/[，,、；;\s]+$/, "") + joiner + text : text;
        }
      } else {
        input.value = value.indexOf(text) >= 0 ? value.replace(text, "") : value + text;
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
  });
  document.querySelectorAll("input, textarea").forEach(function (input) {
    if (!input.id || !document.querySelector('.chip[data-target="' + input.id + '"]')) return;
    refreshChips(input);
    input.addEventListener("input", function () { refreshChips(input); });
  });

  /* ---------- 复诊日期快捷按钮 ---------- */
  document.querySelectorAll("[data-next-days]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var base = document.getElementById("visit_date").value;
      var target = document.getElementById("next_visit_date");
      var d = base ? new Date(base + "T00:00:00") : new Date();
      d.setDate(d.getDate() + parseInt(btn.dataset.nextDays, 10));
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      target.value = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
      target.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });

  /* ---------- 未保存离开提醒 ---------- */
  document.querySelectorAll("form[data-dirty-guard]").forEach(function (form) {
    var dirty = false;
    form.addEventListener("input", function () { dirty = true; });
    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) {
      if (dirty) { e.preventDefault(); e.returnValue = ""; }
    });
  });

  /* ---------- 处方编辑器 ---------- */
  var NOTE_WORDS = ["先煎", "后下", "包煎", "另煎", "烊化", "冲服", "打碎", "研末", "兑服"];
  var UNIT_ALIASES = { "g": "g", "G": "g", "克": "g", "ml": "ml", "mL": "ml", "毫升": "ml" };
  var TOKEN_RE = new RegExp(
    "^(.+?)(\\d+(?:\\.\\d+)?)?\\s*(g|G|克|枚|片|个|只|条|段|粒|ml|mL|毫升)?" +
    "[（(]?(" + NOTE_WORDS.join("|") + ")?[)）]?$"
  );
  var NOTE_ONLY_RE = new RegExp("^[（(]?(" + NOTE_WORDS.join("|") + ")[)）]?$");
  var COUNT_RE = /^[x×*]?(\d+)\s*[剂付]$/;

  function parseQuick(text) {
    var result = { items: [], count: null };
    text.split(/[\s,，、;；]+/).forEach(function (token) {
      if (!token) return;
      var m = token.match(COUNT_RE);
      if (m) { result.count = m[1]; return; }
      m = token.match(NOTE_ONLY_RE);
      if (m) {
        var last = result.items[result.items.length - 1];
        if (last) last.note = m[1];
        return;
      }
      m = token.match(TOKEN_RE);
      if (!m) return;
      result.items.push({
        herb: m[1],
        dose: m[2] || "",
        unit: m[3] ? (UNIT_ALIASES[m[3]] || m[3]) : "g",
        note: m[4] || ""
      });
    });
    return result;
  }

  function compileRules(rules) {
    function re(pattern) {
      try { return new RegExp(pattern); }
      catch (e) { return new RegExp(pattern.replace(/\(\?<!.\)/g, "")); }  // 旧浏览器不支持后行断言
    }
    return (rules || []).map(function (r) {
      return { kind: r.kind, desc: r.desc, a: re(r.a), b: re(r.b) };
    });
  }

  function initHerbEditor(root) {
    var tbody = root.querySelector("[data-herb-rows]");
    var tpl = root.querySelector("template[data-herb-template]");
    var summary = root.querySelector("[data-herb-summary]");
    var warnings = root.querySelector("[data-herb-warnings]");
    var form = root.closest("form");
    var formulas = readJSON("formula-data") || [];
    var rules = compileRules(readJSON("compat-rules"));

    function rows() { return Array.prototype.slice.call(tbody.querySelectorAll("tr")); }
    function field(row, name) { return row.querySelector('[name="herb_' + name + '"]'); }
    function isBlank(row) { return !field(row, "name").value.trim(); }

    function addRow(item, focus) {
      var row = tpl.content.firstElementChild.cloneNode(true);
      if (item) {
        field(row, "name").value = item.herb || "";
        field(row, "dose").value = item.dose == null ? "" : item.dose;
        field(row, "unit").value = item.unit || "g";
        field(row, "note").value = item.note || "";
      }
      tbody.appendChild(row);
      if (focus) field(row, "name").focus();
      return row;
    }

    function addItems(items) {
      // 先填充末尾的空行，再追加
      var blanks = rows().filter(isBlank);
      items.forEach(function (item) {
        var row = blanks.shift();
        if (row) {
          field(row, "name").value = item.herb;
          field(row, "dose").value = item.dose == null ? "" : item.dose;
          field(row, "unit").value = item.unit || "g";
          field(row, "note").value = item.note || "";
        } else {
          addRow(item);
        }
      });
      changed();
    }

    function changed() {
      var names = [];
      var count = 0;
      var grams = 0;
      var seen = {};
      rows().forEach(function (row, i) {
        row.querySelector(".idx").textContent = i + 1;
        var name = field(row, "name").value.trim();
        row.classList.remove("dup");
        if (!name) return;
        count += 1;
        names.push(name);
        if (seen[name]) { row.classList.add("dup"); seen[name].classList.add("dup"); }
        seen[name] = row;
        var dose = parseFloat(field(row, "dose").value);
        if (!isNaN(dose) && (field(row, "unit").value.trim() || "g") === "g") grams += dose;
      });
      if (summary) {
        summary.textContent = count ? "共 " + count + " 味" + (grams ? "，每剂约 " + formatNumber(grams) + " g" : "") : "尚未录入药物";
      }
      if (warnings) {
        var messages = [];
        var dups = Object.keys(seen).filter(function (n) { return names.indexOf(n) !== names.lastIndexOf(n); });
        if (dups.length) messages.push("药物重复：" + dups.join("、"));
        rules.forEach(function (r) {
          var hit = names.filter(function (n) { return r.a.test(n); });
          var hit2 = names.filter(function (n) { return r.b.test(n); });
          if (hit.length && hit2.length) {
            messages.push(r.kind + "：" + hit.concat(hit2).join("、") + "（" + r.desc + "）");
          }
        });
        warnings.innerHTML = "";
        messages.forEach(function (m) {
          var div = document.createElement("div");
          div.textContent = "⚠ " + m;
          warnings.appendChild(div);
        });
        warnings.classList.toggle("hidden", !messages.length);
      }
    }

    root.addEventListener("input", changed);

    root.querySelector("[data-herb-add]").addEventListener("click", function () {
      addRow(null, true);
      changed();
    });

    tbody.addEventListener("click", function (e) {
      if (!e.target.closest("[data-herb-remove]")) return;
      e.target.closest("tr").remove();
      if (!rows().length) addRow();
      changed();
      if (form) form.dispatchEvent(new Event("input"));
    });

    // 回车：药名 → 剂量 → 下一行，避免误提交表单
    tbody.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      var row = e.target.closest("tr");
      if (e.target.name === "herb_name") {
        field(row, "dose").focus();
        return;
      }
      var next = row.nextElementSibling;
      if (next) field(next, "name").focus();
      else { addRow(null, true); changed(); }
    });

    var select = root.querySelector("[data-formula-select]");
    root.querySelectorAll("[data-formula-apply]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var formula = formulas.filter(function (f) { return String(f.id) === select.value; })[0];
        if (!formula) { select.focus(); return; }
        var replace = btn.dataset.formulaApply === "replace";
        if (replace) {
          if (rows().some(function (r) { return !isBlank(r); }) &&
              !window.confirm("将用「" + formula.name + "」替换当前处方中的全部药物，确定吗？")) return;
          tbody.innerHTML = "";
        }
        addItems(formula.items);
        if (!rows().length) addRow();
        var nameInput = form && form.querySelector('[name="formula_name"]');
        if (nameInput && (replace || !nameInput.value.trim())) nameInput.value = formula.name;
        else if (nameInput && nameInput.value.indexOf(formula.name) < 0) nameInput.value += "合" + formula.name;
        var usageInput = form && form.querySelector('[name="usage"]');
        if (usageInput && formula.usage && !usageInput.value.trim()) usageInput.value = formula.usage;
        if (form) form.dispatchEvent(new Event("input"));
      });
    });

    var quickBtn = root.querySelector("[data-quick-parse]");
    if (quickBtn) {
      quickBtn.addEventListener("click", function () {
        var box = root.querySelector("[data-quick-text]");
        var parsed = parseQuick(box.value);
        if (!parsed.items.length && !parsed.count) { box.focus(); return; }
        addItems(parsed.items);
        var countInput = form && form.querySelector('[name="dose_count"]');
        if (parsed.count && countInput) countInput.value = parsed.count;
        box.value = "";
        if (form) form.dispatchEvent(new Event("input"));
      });
    }

    if (!rows().length) addRow();
    changed();
  }

  document.querySelectorAll("[data-herb-editor]").forEach(initHerbEditor);

  /* ---------- 打印页 ---------- */
  var printBtn = document.querySelector("[data-print]");
  if (printBtn) printBtn.addEventListener("click", function () { window.print(); });
})();
