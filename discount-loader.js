// discount-loader.js —— 折现率板块访问门 + 懒加载（随主页始终加载，体积小）
// 该文件不含任何折现率建模逻辑；仅在用户输入正确访问码后动态加载 discount-module.js。
(function () {
  'use strict';

  // 访问码校验：SHA-256(访问码 + 盐)。这两个常量由 set_discount_code.py 生成写入；改码只需重跑该脚本。
  var DISCOUNT_CODE_HASH = "65186a37fd315fd9f7841adc44f207c3ac4d29e8072c81b0f4795883076cb800";
  var DISCOUNT_SALT = "1503da7c604ce27f";

  // ===== 纯 JS SHA-256（输入 UTF-8 字节数组，返回 hex）=====
  var K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];

  function utf8Bytes(str) {
    var out = [];
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
      else if (c < 0xd800 || c >= 0xe000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
      else { i++; var c2 = str.charCodeAt(i); var cp = 0x10000 + ((c & 0x3ff) << 10) + (c2 & 0x3ff); out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f)); }
    }
    return out;
  }

  function sha256Bytes(bytes) {
    var H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    var l = bytes.length;
    var bitLen = l * 8;
    var msg = bytes.slice();
    msg.push(0x80);
    while (msg.length % 64 !== 56) msg.push(0);
    var hi = Math.floor(bitLen / 0x100000000);
    var lo = bitLen >>> 0;
    msg.push((hi >>> 24) & 255, (hi >>> 16) & 255, (hi >>> 8) & 255, hi & 255,
             (lo >>> 24) & 255, (lo >>> 16) & 255, (lo >>> 8) & 255, lo & 255);
    function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
    for (var off = 0; off < msg.length; off += 64) {
      var w = new Array(64);
      for (var i = 0; i < 16; i++) {
        var j = off + i * 4;
        w[i] = (msg[j] << 24) | (msg[j + 1] << 16) | (msg[j + 2] << 8) | msg[j + 3];
      }
      for (i = 16; i < 64; i++) {
        var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (i = 0; i < 64; i++) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (h + S1 + ch + K[i] + w[i]) | 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }
    var hex = '';
    for (i = 0; i < 8; i++) hex += ('00000000' + (H[i] >>> 0).toString(16)).slice(-8);
    return hex;
  }

  function verify(code) {
    if (!code) return false;
    return sha256Bytes(utf8Bytes(code + DISCOUNT_SALT)) === DISCOUNT_CODE_HASH;
  }

  // ===== 懒加载 discount-module.js =====
  var moduleLoading = null;
  function loadModule() {
    if (moduleLoading) return moduleLoading;
    moduleLoading = new Promise(function (resolve, reject) {
      var sc = document.createElement('script');
      sc.src = 'discount-module.js' + (window.location.search ? window.location.search : ('?_=' + Date.now()));
      sc.onload = function () { resolve(); };
      sc.onerror = function () { moduleLoading = null; reject(new Error('discount-module.js 加载失败')); };
      document.head.appendChild(sc);
    });
    return moduleLoading;
  }

  // ===== 访问浮层 =====
  var overlay = null;
  var pendingCb = null;
  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'discountGate';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(15,23,42,.82);';
    overlay.innerHTML =
      '<div style="background:#fff;border-radius:14px;padding:28px 30px;width:340px;max-width:90vw;box-shadow:0 12px 40px rgba(0,0,0,.35);font-family:system-ui,-apple-system,\'Segoe UI\',Roboto,sans-serif;">' +
      '<div style="font-size:17px;font-weight:700;color:#0f172a;margin-bottom:6px;">折现率曲线板块</div>' +
      '<div style="font-size:12.5px;color:#64748b;margin-bottom:16px;line-height:1.5;">该板块含专有折现率建模逻辑，需输入访问码查看。本次访问（同一浏览器标签）内无需重复输入。</div>' +
      '<input id="discountGateInput" type="password" placeholder="请输入访问码" autocomplete="off" style="width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;outline:none;" />' +
      '<div id="discountGateErr" style="color:#dc2626;font-size:12px;height:16px;margin:6px 2px 0;"></div>' +
      '<button id="discountGateBtn" style="margin-top:12px;width:100%;padding:10px;border:none;border-radius:8px;background:#2563eb;color:#fff;font-size:14px;font-weight:600;cursor:pointer;">进入</button>' +
      '<div style="margin-top:10px;text-align:right;"><a href="#" id="discountGateCancel" style="font-size:12px;color:#94a3b8;text-decoration:none;">取消</a></div>' +
      '</div>';
    document.body.appendChild(overlay);
    var input = overlay.querySelector('#discountGateInput');
    var err = overlay.querySelector('#discountGateErr');
    var btn = overlay.querySelector('#discountGateBtn');
    var cancel = overlay.querySelector('#discountGateCancel');
    function submit() {
      var code = input.value;
      if (verify(code)) {
        try { sessionStorage.setItem('discount_unlocked', '1'); } catch (e) {}
        overlay.style.display = 'none';
        loadModule().then(function () { if (pendingCb) { var cb = pendingCb; pendingCb = null; cb(); } })
                    .catch(function () { err.textContent = '模块加载失败，请刷新重试'; });
      } else {
        err.textContent = '访问码错误，请重试';
        input.value = '';
        input.focus();
      }
    }
    btn.addEventListener('click', submit);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
    cancel.addEventListener('click', function (e) {
      e.preventDefault();
      overlay.style.display = 'none';
      pendingCb = null;
      if (typeof showMainView === 'function') showMainView('monitor');
    });
    setTimeout(function () { input.focus(); }, 30);
    return overlay;
  }

  // 入口：每次切换到折现率板块都弹码门（输对后加载模块并执行回调，不留免输入记录）。
  // 设计权衡：若需要"本次访问免重复"，把下方 if (true) 改为 if (sessionStorage.getItem('discount_unlocked')==='1') 即可。
  function requestDiscountAccess(cb) {
    pendingCb = cb;
    var ov = ensureOverlay();
    // 复位输入框与错误提示（防止上次输错残留）
    var inp = ov.querySelector('#discountGateInput');
    var err = ov.querySelector('#discountGateErr');
    if (inp) inp.value = '';
    if (err) err.textContent = '';
    ov.style.display = 'flex';
    setTimeout(function () { if (inp) inp.focus(); }, 30);
  }

  window.requestDiscountAccess = requestDiscountAccess;
})();
