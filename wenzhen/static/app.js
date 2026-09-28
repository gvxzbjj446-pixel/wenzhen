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

  /* 桌面版由 pywebview 提供本机功能接口；在浏览器中打开时为空 */
  function desktopApi() {
    return window.pywebview && window.pywebview.api;
  }

  /* ---------- 页面底部提示条 ---------- */
  function toast(message, kind) {
    var box = document.createElement("div");
    box.className = "toast" + (kind === "error" ? " toast-error" : "");
    box.setAttribute("role", "status");
    box.textContent = message;
    document.body.appendChild(box);
    setTimeout(function () { box.classList.add("show"); }, 10);
    setTimeout(function () {
      box.classList.remove("show");
      setTimeout(function () { box.remove(); }, 300);
    }, kind === "error" ? 6000 : 4000);
  }
  window.wenzhenToast = toast;

  function fail() { toast("操作失败，请重试。", "error"); }

  /* ---------- 确认对话框（代替浏览器自带的 confirm 弹窗） ---------- */
  function confirmDialog(message, options) {
    options = options || {};
    return new Promise(function (resolve) {
      var overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.innerHTML =
        '<div class="modal" role="alertdialog" aria-modal="true">' +
        '<p class="modal-message"></p><div class="modal-actions">' +
        '<button type="button" class="btn" data-answer="no"></button>' +
        '<button type="button" class="btn" data-answer="yes"></button></div></div>';
      overlay.querySelector(".modal-message").textContent = message;
      var yes = overlay.querySelector('[data-answer="yes"]');
      var no = overlay.querySelector('[data-answer="no"]');
      yes.textContent = options.ok || "确定";
      yes.classList.add(options.danger ? "btn-danger-solid" : "btn-primary");
      no.textContent = options.cancel || "取消";

      function close(answer) {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        resolve(answer);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); close(false); }
      }
      overlay.addEventListener("click", function (e) {
        var answer = e.target.getAttribute("data-answer");
        if (answer) close(answer === "yes");
        else if (e.target === overlay) close(false);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(overlay);
      (options.danger ? no : yes).focus();
    });
  }
  window.wenzhenConfirm = confirmDialog;

  /* ---------- 删除等危险操作二次确认 ---------- */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (!msg || form.dataset.confirmed) return;
    e.preventDefault();
    confirmDialog(msg, { ok: form.getAttribute("data-confirm-ok") || "删除", danger: true })
      .then(function (yes) {
        if (!yes) return;
        form.dataset.confirmed = "1";
        form.submit();
      });
  });

  /* ---------- 快选标签：点击填入，再点取消 ---------- */
  function segments(value) {
    return value.split(/[，,、；;\s]+/).filter(Boolean);
  }

  // data-replace：单选，点击即替换整个内容（如频次、类别）
  function chipActive(input, chip) {
    var text = chip.textContent;
    var value = input.value;
    if (chip.hasAttribute("data-replace")) return value.trim() === text;
    return chip.dataset.joiner ? segments(value).indexOf(text) >= 0 : value.indexOf(text) >= 0;
  }

  function refreshChips(input) {
    document.querySelectorAll('.chip[data-target="' + input.id + '"]').forEach(function (chip) {
      chip.classList.toggle("on", chipActive(input, chip));
    });
  }

  function toggleText(input, text, joiner, replace) {
    var value = input.value.trim();
    if (replace) {
      input.value = value === text ? "" : text;
    } else if (joiner) {
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
  }

  document.querySelectorAll(".chip[data-target]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      toggleText(document.getElementById(chip.dataset.target), chip.textContent,
                 chip.dataset.joiner, chip.hasAttribute("data-replace"));
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
  var dirty = false;
  var UNSAVED = "当前填写的内容还没有保存，";

  function setDirty(value) {
    dirty = value;
    var api = desktopApi();
    if (api && api.set_dirty) api.set_dirty(value);
  }

  document.querySelectorAll("form[data-dirty-guard]").forEach(function (form) {
    form.addEventListener("input", function () { if (!dirty) setDirty(true); });
    form.addEventListener("submit", function () { setDirty(false); });
  });
  window.addEventListener("pywebviewready", function () { setDirty(dirty); });
  window.addEventListener("beforeunload", function (e) {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  // 点击站内链接离开时，用对话框确认
  document.addEventListener("click", function (e) {
    if (!dirty || e.defaultPrevented) return;
    var link = e.target.closest("a[href]");
    if (!link || link.target || link.hasAttribute("data-download") ||
        link.getAttribute("href").charAt(0) === "#") return;
    e.preventDefault();
    confirmDialog(UNSAVED + "确定要离开吗？", { ok: "离开", cancel: "继续填写", danger: true })
      .then(function (yes) {
        if (!yes) return;
        setDirty(false);
        location.href = link.href;
      });
  });

  // 桌面版关闭窗口时调用
  window.wenzhenConfirmQuit = function () {
    confirmDialog(UNSAVED + "确定要退出吗？", { ok: "退出", cancel: "继续填写", danger: true })
      .then(function (yes) { if (yes) desktopApi().confirm_quit(); });
  };

  /* ---------- 桌面版：导出文件用“另存为”对话框保存 ---------- */
  function filenameFrom(disposition) {
    var m = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
    if (m) return decodeURIComponent(m[1]);
    m = /filename="?([^";]+)"?/i.exec(disposition || "");
    return m ? m[1] : "";
  }

  function blobToDataURL(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  document.addEventListener("click", function (e) {
    var link = e.target.closest("a[data-download]");
    var api = desktopApi();
    if (!link || !api) return;  // 浏览器中照常下载
    e.preventDefault();
    fetch(link.href, { credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        var name = filenameFrom(resp.headers.get("Content-Disposition")) || link.getAttribute("data-download");
        return resp.blob().then(blobToDataURL).then(function (data) { return api.save_file(name, data); });
      })
      .then(function (path) { if (path) toast("已保存到：" + path); })
      .catch(fail);
  });

  /* ---------- 桌面版：备份、恢复、打开数据文件夹 ---------- */
  function restoreFromBackup() {
    var api = desktopApi();
    if (!api) return;
    api.choose_backup().then(function (info) {
      if (!info) return;
      if (!info.ok) { toast(info.message, "error"); return; }
      var message = "将用所选备份替换当前的全部数据。\n\n" +
        (info.created_at ? "备份时间：" + info.created_at + "\n" : "") +
        "备份中有患者 " + info.patients + " 人、就诊记录 " + info.visits + " 条" +
        (info.therapy_sessions ? "、理疗记录 " + info.therapy_sessions + " 次" : "") + "。\n" +
        "当前数据会先自动另存一份，以防万一。\n\n确定恢复吗？";
      return confirmDialog(message, { ok: "恢复", danger: true }).then(function (yes) {
        if (!yes) return;
        return api.restore_database(info.path).then(function (result) {
          if (!result.ok) { toast(result.message, "error"); return; }
          try { sessionStorage.setItem("wenzhen-notice", "数据已恢复，请重新登录。"); } catch (err) { /* 忽略 */ }
          var logout = document.querySelector("form[data-logout]");
          if (logout) logout.submit(); else location.href = "/";
        });
      });
    }).catch(fail);
  }
  window.wenzhenRestore = restoreFromBackup;

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-desktop-action]");
    var api = desktopApi();
    if (!btn || !api) return;
    e.preventDefault();
    var action = btn.getAttribute("data-desktop-action");
    if (action === "backup") {
      api.backup_database().then(function (path) { if (path) toast("已导出完整数据包：" + path); }).catch(fail);
    } else if (action === "save-copy") {
      api.save_backup_copy(btn.getAttribute("data-name"))
        .then(function (path) { if (path) toast("已另存到：" + path); }).catch(fail);
    } else if (action === "restore") {
      restoreFromBackup();
    } else if (action === "choose-mirror") {
      api.choose_mirror_folder().then(function (result) {
        if (!result) return;
        try { sessionStorage.setItem("wenzhen-notice", result.message); } catch (err) { /* 忽略 */ }
        location.reload();
      }).catch(fail);
    } else if (action === "open-folder") {
      api.open_data_folder().catch(fail);
    } else if (action === "open-backups") {
      api.open_backup_folder().catch(fail);
    }
  });

  try {
    var notice = sessionStorage.getItem("wenzhen-notice");
    if (notice) {
      sessionStorage.removeItem("wenzhen-notice");
      toast(notice);
    }
  } catch (err) { /* 浏览器禁用存储时忽略 */ }

  /* ---------- 快捷键：F5 刷新、Ctrl+P 打印（桌面版窗口没有浏览器快捷键） ---------- */
  document.addEventListener("keydown", function (e) {
    if (e.key === "F5" && desktopApi()) {
      e.preventDefault();
      location.reload();
    } else if ((e.ctrlKey || e.metaKey) && (e.key === "p" || e.key === "P") &&
               document.querySelector("[data-print]")) {
      e.preventDefault();
      window.print();
    }
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

    function applyFormula(formula, replace) {
      if (replace) tbody.innerHTML = "";
      addItems(formula.items);
      if (!rows().length) addRow();
      var nameInput = form && form.querySelector('[name="formula_name"]');
      if (nameInput && (replace || !nameInput.value.trim())) nameInput.value = formula.name;
      else if (nameInput && nameInput.value.indexOf(formula.name) < 0) nameInput.value += "合" + formula.name;
      var usageInput = form && form.querySelector('[name="usage"]');
      if (usageInput && formula.usage && !usageInput.value.trim()) usageInput.value = formula.usage;
      if (form) form.dispatchEvent(new Event("input"));
    }

    root.querySelectorAll("[data-formula-apply]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var formula = formulas.filter(function (f) { return String(f.id) === select.value; })[0];
        if (!formula) { select.focus(); return; }
        var replace = btn.dataset.formulaApply === "replace";
        if (!replace || !rows().some(function (r) { return !isBlank(r); })) {
          applyFormula(formula, replace);
          return;
        }
        confirmDialog("将用「" + formula.name + "」替换当前处方中的全部药物，确定吗？", { ok: "替换" })
          .then(function (yes) { if (yes) applyFormula(formula, true); });
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

  /* ---------- 理疗项目编辑：默认时长、穴位快选 ---------- */
  function initItemEditor(root) {
    var tbody = root.querySelector("[data-item-rows]");
    var tpl = root.querySelector("template[data-item-template]");
    var summary = root.querySelector("[data-item-summary]");
    var targetLabel = root.querySelector("[data-site-target]");
    var chips = Array.prototype.slice.call(root.querySelectorAll("[data-site-chip]"));
    var form = root.closest("form");
    var minutes = readJSON("therapy-minutes") || {};
    var active = null;
    var ORDER = ["therapy", "site", "minutes", "note"];

    function rows() { return Array.prototype.slice.call(tbody.querySelectorAll("tr")); }
    function field(row, name) { return row.querySelector('[name="item_' + name + '"]'); }

    function addRow(focus) {
      var row = tpl.content.firstElementChild.cloneNode(true);
      tbody.appendChild(row);
      if (focus) field(row, "therapy").focus();
      return row;
    }

    // 穴位快选填入“当前行”：最近点过的一行，默认第一行
    function refresh() {
      var list = rows();
      var count = 0;
      var total = 0;
      list.forEach(function (row, i) {
        row.querySelector(".idx").textContent = i + 1;
        if (!field(row, "therapy").value.trim()) return;
        count += 1;
        var m = parseInt(field(row, "minutes").value, 10);
        if (!isNaN(m)) total += m;
      });
      if (summary) {
        summary.textContent = count ? "共 " + count + " 项" + (total ? "，约 " + total + " 分钟" : "") : "尚未填写治疗项目";
      }
      if (list.indexOf(active) < 0) active = list[0] || null;
      list.forEach(function (row) { row.classList.toggle("active", row === active); });
      if (!active) return;
      var name = field(active, "therapy").value.trim();
      targetLabel.textContent = "第 " + (list.indexOf(active) + 1) + " 行" + (name ? "（" + name + "）" : "");
      var site = field(active, "site");
      chips.forEach(function (chip) {
        chip.classList.toggle("on", segments(site.value).indexOf(chip.textContent) >= 0);
      });
    }

    tbody.addEventListener("focusin", function (e) {
      var row = e.target.closest("tr");
      if (row && row !== active) { active = row; refresh(); }
    });

    root.addEventListener("input", function (e) {
      var row = e.target.closest("tr");
      // 选好项目后自动填入默认时长；时长是自动填的才跟着项目改
      if (row && e.target.name === "item_therapy") {
        var box = field(row, "minutes");
        var preset = minutes[e.target.value.trim()];
        if (preset && (!box.value || box.value === row.dataset.autoMinutes)) {
          box.value = preset;
          row.dataset.autoMinutes = String(preset);
        }
      }
      refresh();
    });

    root.querySelector("[data-item-add]").addEventListener("click", function () {
      active = addRow(true);
      refresh();
    });

    tbody.addEventListener("click", function (e) {
      if (!e.target.closest("[data-item-remove]")) return;
      e.target.closest("tr").remove();
      if (!rows().length) addRow();
      refresh();
      if (form) form.dispatchEvent(new Event("input"));
    });

    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        if (!active) active = rows()[0] || addRow();
        toggleText(field(active, "site"), chip.textContent, "、");
      });
    });

    // 回车跳到下一格，最后一格跳到下一行（没有则新增），避免误提交表单
    tbody.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.isComposing || !e.target.name) return;
      e.preventDefault();
      var row = e.target.closest("tr");
      var i = ORDER.indexOf(e.target.name.replace("item_", ""));
      if (i >= 0 && i < ORDER.length - 1) { field(row, ORDER[i + 1]).focus(); return; }
      var next = row.nextElementSibling || addRow();
      field(next, "therapy").focus();
      refresh();
    });

    if (!rows().length) addRow();
    refresh();
  }

  document.querySelectorAll("[data-item-editor]").forEach(initItemEditor);

  /* ---------- 疼痛评分变化图：悬停显示该次评分 ---------- */
  document.querySelectorAll("[data-pain-chart]").forEach(function (fig) {
    var svg = fig.querySelector("svg");
    var tip = fig.querySelector("[data-chart-tip]");
    var cross = fig.querySelector("[data-crosshair]");
    var points = JSON.parse(fig.querySelector("[data-chart-points]").textContent);
    var width = svg.viewBox.baseVal.width;
    var SERIES = [["before", "治疗前"], ["after", "治疗后"]];

    function hide() {
      tip.hidden = true;
      cross.setAttribute("visibility", "hidden");
    }

    fig.querySelectorAll("rect.hit").forEach(function (rect) {
      rect.addEventListener("mouseenter", function () {
        var p = points[parseInt(rect.dataset.i, 10)];
        var x = parseFloat(rect.dataset.x);
        cross.setAttribute("x1", x);
        cross.setAttribute("x2", x);
        cross.setAttribute("visibility", "visible");
        tip.textContent = "";
        var head = document.createElement("div");
        head.className = "tip-head";
        head.textContent = p.label + (p.date ? " · " + p.date : "");
        tip.appendChild(head);
        SERIES.forEach(function (s) {
          if (p[s[0]] === null || p[s[0]] === undefined) return;
          var line = document.createElement("div");
          var key = document.createElement("i");
          key.className = "key key-" + s[0];
          line.appendChild(key);
          line.appendChild(document.createTextNode(s[1] + "　" + p[s[0]]));
          tip.appendChild(line);
        });
        tip.hidden = false;
        var px = x / width * svg.clientWidth;
        var right = px > svg.clientWidth / 2;
        tip.style.left = right ? "" : (px + 12) + "px";
        tip.style.right = right ? (svg.clientWidth - px + 12) + "px" : "";
      });
    });
    svg.addEventListener("mouseleave", hide);
  });

  /* ---------- 打印页 ---------- */
  var printBtn = document.querySelector("[data-print]");
  if (printBtn) printBtn.addEventListener("click", function () { window.print(); });

  // 放在最后：前面的初始化都成功才标记就绪，否则页面会显示“脚本没有正常运行”的提示
  document.documentElement.classList.add("js-ready");
})();
