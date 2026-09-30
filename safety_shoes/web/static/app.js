// 移动端：为每个 td 注入对应表头文本（配合 CSS ::before 生成堆叠卡片）
(function () {
  function labelize() {
    document.querySelectorAll('table.tbl').forEach(function (tbl) {
      var ths = tbl.querySelectorAll('thead th');
      if (!ths.length) return;
      tbl.querySelectorAll('tbody tr').forEach(function (tr) {
        Array.prototype.forEach.call(tr.children, function (td, i) {
          if (ths[i] && !td.getAttribute('data-label')) {
            td.setAttribute('data-label', ths[i].textContent.trim());
          }
        });
      });
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', labelize);
  } else {
    labelize();
  }
})();
