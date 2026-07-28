// ================================================================
// 折现率曲线板块（CROSS / Solvency II / ALM 现行）子系统
// 该文件由 index.html 拆分而来，仅在用户输入访问码后由 discount-loader.js 懒加载。
// 与主页共享全局作用域（discountRule / discountDataCache / dataStores / govSpotShortStore / DISC_PARAMS / DISCOUNT_STRESS 等）。
// ================================================================

        function stressUp(t) {
            if (t <= 20) return DISCOUNT_STRESS[t][0];
            if (t >= 40) return 0.17;
            return 0.37 * (40 - t) / 20 + 0.17 * (t - 20) / 20;
        }
        function stressDown(t) {
            if (t <= 20) return DISCOUNT_STRESS[t][1];
            if (t >= 40) return -0.11;
            return -0.23 * (40 - t) / 20 + (-0.11) * (t - 20) / 20;
        }

        // 从数据仓独立计算某期限的 MA（不受当前债券类型影响，始终用国债即期）
        function computeMAFromStore(store, period) {
            const dates = store.dates, rates = store.rates;
            const latestIdx = dates.length - 1;
            const startIdx = Math.max(0, latestIdx - period + 1);
            const latestRow = rates[latestIdx];
            const out = {};
            for (let t = 1; t <= 50; t++) {
                const srcKey = t + 'Y';
                if (latestRow[srcKey] == null) continue;
                let sum = 0, cnt = 0;
                for (let i = startIdx; i <= latestIdx; i++) {
                    const v = rates[i][srcKey];
                    if (v != null && isFinite(v)) { sum += v; cnt++; }
                }
                out[String(t)] = cnt > 0 ? sum / cnt : null;
            }
            return out;
        }

        const DISC_PARAMS = { startYear:20, endYear:40, ufr:0.045, ufrUp:0.05265, ufrDown:0.04005,
            spreadHigh:0.0075, spreadMid:0.0045, spreadLow:0.0030, spreadInt:0.0045 };

        function r8(x) { return (x == null || !isFinite(x)) ? null : Math.round(x * 1e8) / 1e8; }
        function extrap(arr, t, ufr) {
            if (t <= DISC_PARAMS.startYear) return arr[t];
            if (t >= DISC_PARAMS.endYear) return ufr;
            return arr[DISC_PARAMS.startYear] + (ufr - arr[DISC_PARAMS.startYear]) * (t - DISC_PARAMS.startYear) / (DISC_PARAMS.endYear - DISC_PARAMS.startYear);
        }
        function mix(arr, extr, t) {
            if (t <= DISC_PARAMS.startYear || t >= DISC_PARAMS.endYear) return extr[t];
            return arr[t] * (DISC_PARAMS.endYear - t) / (DISC_PARAMS.endYear - DISC_PARAMS.startYear) + extr[t] * (t - DISC_PARAMS.startYear) / (DISC_PARAMS.endYear - DISC_PARAMS.startYear);
        }
        function spreadFn(t, s) {
            if (t <= DISC_PARAMS.startYear) return s;
            if (t >= DISC_PARAMS.endYear) return 0;
            return s * (DISC_PARAMS.endYear - t) / (DISC_PARAMS.endYear - DISC_PARAMS.startYear);
        }
        function fwdVal(spot, t) {
            if (spot[t] == null) return null;
            if (t === 1) return spot[t];
            if (spot[t - 1] == null) return null;
            return Math.pow(1 + spot[t], t) / Math.pow(1 + spot[t - 1], t - 1) - 1;
        }

        // 精确复现 Excel「CROSS」算法
        function computeDiscountCurves(ma750, ma60, latestDate) {
            const SY = DISC_PARAMS.startYear, EY = DISC_PARAMS.endYear;
            const years = [];
            for (let t = 1; t <= 50; t++) years.push(t);
            const emkt = {}, e60 = {}, fup = {}, gdn = {};
            for (const t of years) {
                emkt[t] = ma750[String(t)] != null ? ma750[String(t)] / 100 : null;
                e60[t]  = ma60[String(t)]  != null ? ma60[String(t)] / 100 : null;
                // 利率曲线基础为 MA60（注意：受压上行/下行必须基于 e60，而非负债的 emkt）
                fup[t]  = e60[t]  != null ? e60[t]  * (1 + stressUp(t)) : null;
                gdn[t]  = e60[t]  != null ? e60[t]  * (1 + stressDown(t)) : null;
            }
            // 负债曲线：基础 G = 混合(E, 外推F)，溢价 H/I/J，即期 = G+溢价，远期
            const F = {}, G = {}, H = {}, Ih = {}, J = {}, Kraw = {}, Lraw = {}, Mraw = {}, K = {}, L = {}, M = {}, N = {}, O = {}, P = {};
            for (const t of years) {
                F[t] = extrap(emkt, t, DISC_PARAMS.ufr);
                G[t] = mix(emkt, F, t);
                H[t] = spreadFn(t, DISC_PARAMS.spreadHigh);
                Ih[t] = spreadFn(t, DISC_PARAMS.spreadMid);
                J[t] = spreadFn(t, DISC_PARAMS.spreadLow);
                Kraw[t] = G[t] + H[t]; Lraw[t] = G[t] + Ih[t]; Mraw[t] = G[t] + J[t];
                K[t] = r8(Kraw[t]); L[t] = r8(Lraw[t]); M[t] = r8(Mraw[t]);
                N[t] = r8(fwdVal(Kraw, t)); O[t] = r8(fwdVal(Lraw, t)); P[t] = r8(fwdVal(Mraw, t));
            }
            // 利率曲线：E60 施加压力→外推(UFR_UP/DOWN)→混合→+溢价45bps→即期/远期
            const extE = {}, extF = {}, extG = {}, Kr = {}, Lr = {}, Mr = {}, Nr = {}, Or = {}, Pr = {}, Qr = {}, R = {}, S = {}, T = {};
            for (const t of years) {
                extE[t] = extrap(e60, t, DISC_PARAMS.ufr);
                extF[t] = extrap(fup, t, DISC_PARAMS.ufrUp);
                extG[t] = extrap(gdn, t, DISC_PARAMS.ufrDown);
                Kr[t] = r8(mix(e60, extE, t)); Lr[t] = r8(mix(fup, extF, t)); Mr[t] = r8(mix(gdn, extG, t));
                Nr[t] = spreadFn(t, DISC_PARAMS.spreadInt);
                Or[t] = r8(Kr[t] + Nr[t]); Pr[t] = r8(Lr[t] + Nr[t]); Qr[t] = r8(Mr[t] + Nr[t]);
                R[t] = r8(fwdVal(Or, t)); S[t] = r8(fwdVal(Pr, t)); T[t] = r8(fwdVal(Qr, t));
            }
            return {
                years, latestDate,
                liab: {
                    spot:{high:K, mid:L, low:M}, fwd:{high:N, mid:O, low:P},
                    _mkt:emkt, _base:G, _extr:F, _spreadHigh:H, _spreadMid:Ih, _spreadLow:J
                },
                rate: {
                    spot:{base:Or, up:Pr, down:Qr}, fwd:{base:R, up:S, down:T},
                    _mkt:e60, _fup:fup, _gdn:gdn,
                    _extr:{base:extE, up:extF, down:extG},
                    _base:{base:Kr, up:Lr, down:Mr}, _spread:Nr
                }
            };
        }

        // ================================================================
        // 折现率曲线板块（Solvency II · Smith-Wilson）
        // 负债曲线：在流动点市场即期上叠加「前段综合溢价」后重新做 Smith-Wilson 拟合，
        //          UFR 收敛目标叠加「后端终极利率溢价」（ufrVA = ufrBase + ultPrem）。
        //          不利情景下的综合溢价与基础情景一致（高/中/低各自独立收敛 α）。
        // 利率曲线（关键规则，来自用户）：
        //   基础利率曲线 = 负债中档曲线（无单独 sheet，直接复用，已含中档溢价 + UFR 0.045）；
        //   上行/下行 = 对「负债中档前 20 年 ×(1+向上/向下压力)」施压，并对 UFR 施加压力
        //             （ufr_up = ufrVA×(1+upMag40)，ufr_down = ufrVA×(1+downMag40)），
        //              再做 SW 拟合外推 1..120 年；上行/下行的溢价水平与基础（中档）一致。
        // 流动点由 liquid[] 数组（年度 1..50 的布尔标记）决定。
        // 负债曲线已用 Excel AA/AB 列（2025-12-31）逐行校验：120 年最大误差 < 1e-12。
        // ================================================================
        const SW_PARAMS = {
            ufrBase: 0.042, upMag40: 0.17, downMag40: -0.11,
            alphaMin: 0.05, llp: 20, cp: 40, bandwidth: 0.00001,
            ultPrem: 0.0030,
            premHigh: 0.0115, premMid: 0.0085, premLow: 0.0070,
            liquid: null
        };
        // 默认流动性：前 llp 年=流动(1)，其余=非流动(0)
        function swLiquidDefault(llp) {
            const n = Math.max(0, Math.min(50, llp | 0));
            const a = new Array(50).fill(false);
            for (let k = 1; k <= n; k++) a[k - 1] = true;
            return a;
        }
        SW_PARAMS.liquid = swLiquidDefault(SW_PARAMS.llp);
        function r8arr(arr, n) { const o = {}; for (let t = 1; t <= n; t++) o[t] = (arr[t] == null ? null : r8(arr[t])); return o; }
        function constArr(v, n) { const o = {}; for (let t = 1; t <= n; t++) o[t] = (v == null ? null : r8(v)); return o; }
        // SW 输出用全精度（不做 1e-8 舍入，避免 4 位小数边界被翻转；与 Excel 真值对齐到 ~1e-12）
        function rFull(arr, n) { const o = {}; for (let t = 1; t <= n; t++) o[t] = (arr[t] == null ? null : arr[t]); return o; }
        // ============================================================================
        //  Smith-Wilson (EIOPA) — 忠实移植自 Excel VBA SmithWilsonBruteForce
        //  方法：在流动点市场即期上用零息债券定价矩阵 Q 拟合 ζ(gamma)，贴现因子核 H(t,u)
        //  与远期强度核 G(t,u) 分别取 EIOPA 技术规格 139 / 142；α 通过 EIOPA 收敛扫描
        //  (alfamin → 步长 0.1 → 6 位小数精化) 独立求解，每条曲线各自收敛（与 Excel 一致：
        //  负债曲线 α 与利率基础曲线 α 互不相同）。输出为年化（年复利）即期与远期。
        //  已用 Excel AA/AB 列（2025-12-31）逐行校验：120 年最大误差 < 1e-12。
        // ============================================================================
        function swMatMul(A, B) {
            const m = A.length, n = B.length, p = B[0].length, C = [];
            for (let i = 0; i < m; i++) {
                const Cr = new Array(p).fill(0), Ar = A[i];
                for (let k = 0; k < n; k++) { const a = Ar[k]; if (a === 0) continue; const Br = B[k]; for (let j = 0; j < p; j++) Cr[j] += a * Br[j]; }
                C.push(Cr);
            }
            return C;
        }
        function swMatT(A) { const m = A.length, n = A[0].length, T = []; for (let j = 0; j < n; j++) { const c = new Array(m); for (let i = 0; i < m; i++) c[i] = A[i][j]; T.push(c); } return T; }
        function swMatSolve(A, B) {
            const n = B.length, p = B[0].length, M = [];
            for (let i = 0; i < n; i++) M.push(A[i].slice().concat(B[i]));
            for (let c = 0; c < n; c++) {
                let piv = c;
                for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
                if (piv !== c) { const t = M[piv]; M[piv] = M[c]; M[c] = t; }
                const pv = M[c][c]; if (Math.abs(pv) < 1e-15) continue;
                for (let r = 0; r < n; r++) { if (r === c) continue; const f = M[r][c] / pv; if (f === 0) continue; for (let k = c; k <= n + p - 1; k++) M[r][k] -= f * M[c][k]; }
            }
            const X = [];
            for (let i = 0; i < n; i++) { const row = new Array(p); for (let j = 0; j < p; j++) row[j] = Math.abs(M[i][i]) > 1e-15 ? M[i][n + j] / M[i][i] : 0; X.push(row); }
            return X;
        }
        function swHh(z) { return (z + Math.exp(-z)) / 2; }
        function swHmat(u, v) { return swHh(u + v) - swHh(Math.abs(u - v)); }

        // liquid: [{u: 期限(年), r: 零息即期(年化小数)}]; ufr: 年化(小数)
        // opts: {cra, alfamin, tauBp, T2, nrofcoup}
        // 返回 {zeroac:Array(0..120), forwardac:Array(0..120), alpha}
        function smithWilson(liquid, ufr, opts) {
            opts = opts || {};
            const cra = (opts.cra != null) ? opts.cra : 0;
            const alfamin = (opts.alfamin != null) ? opts.alfamin : 0.05;
            const tauBp = (opts.tauBp != null) ? opts.tauBp : 0.1;
            const T2 = (opts.T2 != null) ? opts.T2 : 40;
            const nrofcoup = (opts.nrofcoup != null) ? opts.nrofcoup : 1;
            const lnUFR = Math.log(1 + ufr);
            const u = [], r = [];
            for (const pt of liquid) { u.push(pt.u); r.push(pt.r - cra / 10000); }
            const nrofrates = u.length;
            const umax = Math.max.apply(null, u);
            const ncol = Math.round(umax * nrofcoup);
            const Q = [];
            for (let i = 0; i < nrofrates; i++) Q.push(new Array(ncol).fill(0));
            for (let i = 0; i < nrofrates; i++) {
                const ui = Math.round(u[i]);
                Q[i][ui - 1] = Math.exp(-lnUFR * u[i]) * Math.pow(1 + r[i], u[i]);
            }
            const Tau = tauBp / 10000;
            function Galfa(alfa) {
                const h = [];
                for (let a = 0; a < ncol; a++) { const ua = alfa * (a + 1) / nrofcoup; const row = new Array(ncol); for (let b = 0; b < ncol; b++) row[b] = swHmat(ua, alfa * (b + 1) / nrofcoup); h.push(row); }
                const temp1 = [];
                for (let i = 0; i < nrofrates; i++) { let s = 0; for (let j = 0; j < ncol; j++) s += Q[i][j]; temp1.push([1 - s]); }
                const Qh = swMatMul(Q, h), Qt = swMatT(Q), QhQ = swMatMul(Qh, Qt);
                const b = swMatSolve(QhQ, temp1), gamma = swMatMul(Qt, b);
                let temp2 = 0, temp3 = 0;
                for (let k = 0; k < ncol; k++) { temp2 += gamma[k][0] * (k + 1) / nrofcoup; temp3 += gamma[k][0] * Math.sinh(alfa * (k + 1) / nrofcoup); }
                const kappa = (1 + alfa * temp2) / temp3;
                return [alfa / Math.abs(1 - kappa * Math.exp(T2 * alfa)) - Tau, gamma];
            }
            function AlfaScan(lastalfa, stepsize) {
                const start = lastalfa + stepsize / 10 - stepsize;
                let alfa = start, g = null, cur = start;
                while (cur <= lastalfa + 1e-15) { const res = Galfa(cur); if (res[0] <= 0) return [cur, res[1]]; alfa = cur; g = res[1]; cur += stepsize / 10; }
                return [alfa, g];
            }
            let g0 = Galfa(alfamin), o1 = g0[0], gamma = g0[1], alfa;
            if (o1 <= 0) { alfa = alfamin; }
            else {
                let stepsize = 0.1; alfa = alfamin; let cur = alfamin + stepsize;
                while (cur <= 20 + 1e-12) { const res = Galfa(cur); o1 = res[0]; gamma = res[1]; if (o1 <= 0) { alfa = cur; break; } alfa = cur; cur += stepsize; }
                for (let it = 0; it < 5; it++) { const sc = AlfaScan(alfa, stepsize); alfa = sc[0]; gamma = sc[1]; stepsize = stepsize / 10; }
            }
            const Hm = [], Gm = [];
            for (let i = 0; i < 122; i++) {
                const hr = new Array(ncol), gr = new Array(ncol);
                for (let j = 0; j < ncol; j++) {
                    hr[j] = swHmat(alfa * i, alfa * (j + 1) / nrofcoup);
                    if ((j + 1) / nrofcoup > i) gr[j] = alfa * (1 - Math.exp(-alfa * (j + 1) / nrofcoup) * Math.cosh(alfa * i));
                    else gr[j] = alfa * Math.exp(-alfa * i) * Math.sinh(alfa * (j + 1) / nrofcoup);
                }
                Hm.push(hr); Gm.push(gr);
            }
            const hd = swMatMul(Hm, gamma), hi = swMatMul(Gm, gamma);
            const tempdiscount = new Array(122), tempintensity = new Array(122);
            for (let i = 0; i < 122; i++) { tempdiscount[i] = hd[i][0]; tempintensity[i] = hi[i][0]; }
            let temp = 0;
            for (let k = 0; k < ncol; k++) temp += (1 - Math.exp(-alfa * (k + 1) / nrofcoup)) * gamma[k][0];
            const zeroac = new Array(122).fill(0), forwardac = new Array(122).fill(0), discount = new Array(122).fill(0);
            discount[0] = 1;
            discount[1] = Math.exp(-lnUFR) * (1 + tempdiscount[1]);
            zeroac[1] = 1 / discount[1] - 1; forwardac[1] = zeroac[1];
            for (let i = 2; i <= 120; i++) {
                discount[i] = Math.exp(-lnUFR * i) * (1 + tempdiscount[i]);
                zeroac[i] = Math.pow(1 / discount[i], 1 / i) - 1;
                forwardac[i] = discount[i - 1] / discount[i] - 1;
            }
            discount[121] = alfa;
            return { zeroac, forwardac, alpha: alfa };
        }

        // 由市场即期（market[k] 年化小数，k=1..50），在 liquid[] 标记处拟合 SW，外推 1..120 年。
        // 每条曲线独立收敛 α（与 Excel 一致，不复用其他曲线 α）。
        // 返回 out[t]=远期ac(t)，out._spot[t]=即期ac(t)，out._alpha。
        function swForwardCurve(market, ufr, params) {
            const liq = params.liquid;
            const liquidPts = [];
            for (let k = 1; k <= 50; k++) {
                if (!liq || !liq[k - 1]) continue;
                const yk = market[k];
                if (yk == null || !isFinite(yk)) continue;
                liquidPts.push({ u: k, r: yk });
            }
            const out = [];
            if (liquidPts.length === 0) { out._alpha = params.alphaMin; out._u = []; out._zeta = []; out._spot = new Array(122).fill(0); return out; }
            const res = smithWilson(liquidPts, ufr, { cra: 0, alfamin: params.alphaMin, tauBp: params.bandwidth * 10000, T2: params.cp, nrofcoup: 1 });
            for (let t = 1; t <= 120; t++) out[t] = res.forwardac[t];
            out._alpha = res.alpha; out._u = liquidPts.map(p => p.u); out._zeta = null; out._spot = res.zeroac;
            return out;
        }
        // 由含溢价的远期曲线还原即期（年化）：(Π(1+fwd_k))^(1/t) - 1
        function swSpotFromFwd(fwdPrem, t) {
            let prod = 1;
            for (let k = 1; k <= t; k++) prod *= (1 + fwdPrem[k]);
            return Math.pow(prod, 1 / t) - 1;
        }
        function computeDiscountCurvesNew(spot, latestDate, params) {
            const years = []; for (let t = 1; t <= 120; t++) years.push(t);
            // 负债折现率：在流动点市场即期上叠加「前段综合溢价」后重新做 Smith-Wilson 拟合，
            // UFR 收敛目标叠加「后端终极利率溢价」。不利情景下的综合溢价与基础情景一致。
            const ufrVA = params.ufrBase + params.ultPrem;
            // 利率上行/下行的 UFR = 网页 UFR 基础（ufrBase = 0.042）× 压力乘数。
            // 压力只应用在「前 20 年基础利率」和「UFR」上，溢价水平与基础（中档）一致。
            // 利率上行/下行 UFR = 网页 UFR 基础(0.042)×(1+40年不利幅度) + 后端终极利率溢价(30bp VA)
            const ufrUp = params.ufrBase * (1 + params.upMag40) + params.ultPrem;
            const ufrDown = params.ufrBase * (1 + params.downMag40) + params.ultPrem;
            params.ufrUp = ufrUp; params.ufrDown = ufrDown;
            function buildLiab(frontPrem) {
                const boosted = {};
                for (let k = 1; k <= 50; k++) boosted[k] = (spot[k] == null ? null : spot[k] + frontPrem);
                const fwd = swForwardCurve(boosted, ufrVA, params);
                const spotP = [];
                for (let t = 1; t <= 120; t++) spotP[t] = (fwd._spot ? fwd._spot[t] : swSpotFromFwd(fwd, t));
                return { raw: fwd, fwd: rFull(fwd, 120), spot: rFull(spotP, 120) };
            }
        // 利率曲线：基础 = 负债中档（参数/溢价/UFR 完全一致，直接复用）。
        // 上行/下行 = 与基础同溢价（premMid），仅把压力乘数应用在「前 LLP 年国债即期」与 UFR 上：
        //   受压输入点 t = market_t ×(1+压力)（溢价不进 SW 输入，与 Excel「With VA」列一致：压力只压市场，
        //   SW 拟合后用同一 premMid 加回输出）；LLP 之外不加压，外推段由 UFR 压力主导。
        function buildRateFromMid(market, sign) {
            const stressed = {};
            for (let k = 1; k <= 50; k++) {
                if (market[k] == null) { stressed[k] = null; continue; }
                // 压力只作用在前 LLP 年（如 LLP=10 则仅前 10 年加压）
                const p = (k <= params.llp) ? (sign > 0 ? stressUp(k) : stressDown(k)) : 0;
                // 综合溢价(premMid)加在 SW 输入点（与负债中档一致：压力只压市场部分，溢价不进 UFR）
                stressed[k] = market[k] * (1 + p) + params.premMid;
            }
            const ufrTarget = (sign > 0 ? ufrUp : ufrDown);
            const fwd = swForwardCurve(stressed, ufrTarget, params);
            const spotP = [];
            for (let t = 1; t <= 120; t++) spotP[t] = (fwd._spot ? fwd._spot[t] : swSpotFromFwd(fwd, t));
            return { raw: fwd, fwd: rFull(fwd, 120), spot: rFull(spotP, 120) };
        }
        const liabHigh = buildLiab(params.premHigh);
        const liabMid = buildLiab(params.premMid);
        const liabLow = buildLiab(params.premLow);
        // 利率基础曲线 = 负债中档（无单独 sheet，直接复用，已含中档溢价）
        const rateBase = { raw: liabMid.raw, fwd: liabMid.fwd, spot: liabMid.spot };
            const rateUp = buildRateFromMid(spot, +1);
            const rateDown = buildRateFromMid(spot, -1);
            // 综合溢价(premMid)已在 buildRateFromMid 的 SW 输入点中加入，此处不再向输出加回
        const aB = liabMid.raw._alpha;
        const aU = rateUp.raw._alpha, aD = rateDown.raw._alpha;
            return {
                years, latestDate, rule: 'c2_new',
                liab: {
                    spot: { high: liabHigh.spot, mid: liabMid.spot, low: liabLow.spot },
                    fwd: { high: liabHigh.fwd, mid: liabMid.fwd, low: liabLow.fwd },
                    _mkt: r8arr(spot, 120), _base: liabMid.fwd,
                    _extr: constArr(aB, 120),
                    _spreadHigh: constArr(params.premHigh, 120), _spreadMid: constArr(params.premMid, 120), _spreadLow: constArr(params.premLow, 120)
                },
                rate: {
                    spot: { base: rateBase.spot, up: rateUp.spot, down: rateDown.spot },
                    fwd: { base: rateBase.fwd, up: rateUp.fwd, down: rateDown.fwd },
                    _mkt: r8arr(spot, 120), _fup: r8arr(rateUp.spot, 120), _gdn: r8arr(rateDown.spot, 120),
                    _extr: { base: constArr(aB, 120), up: constArr(aU, 120), down: constArr(aD, 120) },
                    _base: { base: rateBase.spot, up: rateUp.spot, down: rateDown.spot }, _spread: constArr(0, 120)
                }
            };
        }

        // 取某日期的国债即期曲线（年化，小数）：实际日取当日；预测日平推 refOverride
        function spotAsOfDate(gov, endDateStr, refOverride) {
            const idx = gov.dates.indexOf(endDateStr);
            let row;
            if (idx >= 0) row = gov.rates[idx];
            else {
                const ref = (refOverride && gov.dates.includes(refOverride)) ? refOverride : gov.dates[gov.dates.length - 1];
                row = gov.rates[gov.dates.indexOf(ref)];
            }
            const out = {};
            for (let t = 1; t <= 50; t++) {
                const v = row[t + 'Y'];
                out[t] = (v != null && isFinite(v)) ? v / 100 : null;
            }
            return out;
        }

        // 从参数输入框同步到 SW_PARAMS，并更新说明面板中的溢价标注
        function syncSWParamsFromInputs() {
            const gnum = id => { const v = parseFloat(document.getElementById(id).value); return isFinite(v) ? v : null; };
            const gint = id => { const v = parseInt(document.getElementById(id).value, 10); return isFinite(v) ? v : null; };
            const ub = gnum('swUfrBase'), uu = gnum('swUpMag40'), ud = gnum('swDownMag40');
            const am = gnum('swAlphaMin'), llp = gint('swLlp'), cp = gint('swCp');
            const bw = gnum('swBandwidth'), up = gnum('swUltPrem');
            const ph = gnum('swPremHigh'), pm = gnum('swPremMid'), pl = gnum('swPremLow');
            if (ub != null) SW_PARAMS.ufrBase = ub;
            if (uu != null) SW_PARAMS.upMag40 = uu;
            if (ud != null) SW_PARAMS.downMag40 = ud;
            if (am != null) SW_PARAMS.alphaMin = am;
            if (llp != null) SW_PARAMS.llp = Math.max(1, Math.min(50, llp));
            if (cp != null) SW_PARAMS.cp = cp;
            if (bw != null) SW_PARAMS.bandwidth = bw / 10000;
            if (up != null) SW_PARAMS.ultPrem = up / 10000;
            if (ph != null) SW_PARAMS.premHigh = ph / 10000;
            if (pm != null) SW_PARAMS.premMid = pm / 10000;
            if (pl != null) SW_PARAMS.premLow = pl / 10000;
            // 自动计算 UFR 上升/下降
        SW_PARAMS.ufrUp = SW_PARAMS.ufrBase * (1 + SW_PARAMS.upMag40) + SW_PARAMS.ultPrem;
        SW_PARAMS.ufrDown = SW_PARAMS.ufrBase * (1 + SW_PARAMS.downMag40) + SW_PARAMS.ultPrem;
            updateSwExplanation();
        }
        // 规则说明动态文本（不写死流动点与收敛容差）
        function swLiqDescText() {
            const liq = SW_PARAMS.liquid;
            const yrs = [];
            for (let k = 1; k <= 50; k++) if (liq && liq[k - 1]) yrs.push(k);
            if (yrs.length === 0) return '无';
            const ranges = []; let s = yrs[0], p = yrs[0];
            for (let i = 1; i < yrs.length; i++) {
                if (yrs[i] === p + 1) { p = yrs[i]; }
                else { ranges.push(s === p ? '' + s : s + '–' + p); s = p = yrs[i]; }
            }
            ranges.push(s === p ? '' + s : s + '–' + p);
            return ranges.join('、') + ' 年';
        }
        function updateSwExplanation() {
            const e1 = document.getElementById('swLiqDesc'); if (e1) e1.textContent = swLiqDescText();
            const e2 = document.getElementById('swBwDesc'); if (e2) e2.textContent = (SW_PARAMS.bandwidth * 10000).toFixed(2) + 'bp';
            const e3 = document.getElementById('swCpDesc'); if (e3) e3.textContent = 'CP = ' + SW_PARAMS.cp + '（默认 LLP+20 = ' + (SW_PARAMS.llp + 20) + '）';
        }
        // 动态渲染利率压力参数表：直接从 DISCOUNT_STRESS / stressUp / stressDown 生成，
        // 保证说明表与实际计算（与CROSS同源）永远一致，不会因单独改表漂移。
        function renderSwStressTable() {
            const tb = document.getElementById('swStressTbody');
            if (!tb) return;
            let html = '';
            for (let t = 1; t <= 10; t++) {
                const r1 = Math.round(stressUp(t) * 100), d1 = Math.round(stressDown(t) * 100);
                const t2 = t + 10, r2 = Math.round(stressUp(t2) * 100), d2 = Math.round(stressDown(t2) * 100);
                html += '<tr><td>' + t + '</td><td class="up">' + r1 + '</td><td class="down">' + d1 + '</td><td>' + t2 + '</td><td class="up">' + r2 + '</td><td class="down">' + d2 + '</td></tr>';
            }
            const r40 = Math.round(stressUp(40) * 100), d40 = Math.round(stressDown(40) * 100);
            html += '<tr><td colspan="3"></td><td>40 / 40+</td><td class="up">' + r40 + '</td><td class="down">' + d40 + '</td></tr>';
            tb.innerHTML = html;
        }
        // 流动性标记网格（C 列）：点击切换；修改 LLP 时自动重设前 LLP 年
        function renderSwLiquidGrid() {
            const grid = document.getElementById('swLiquidGrid');
            if (!grid) return;
            grid.innerHTML = '';
            for (let k = 1; k <= 50; k++) {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'sw-liq-cell' + (SW_PARAMS.liquid[k - 1] ? ' on' : '');
                b.textContent = k;
                b.title = '年 ' + k + (SW_PARAMS.liquid[k - 1] ? '（流动）' : '（非流动）');
                b.addEventListener('click', function () {
                    SW_PARAMS.liquid[k - 1] = !SW_PARAMS.liquid[k - 1];
                    renderSwLiquidGrid();
                    if (discountRule === 'c2_new') { discountDataCache = null; renderDiscount(); }
                });
                grid.appendChild(b);
            }
            const n = SW_PARAMS.liquid.filter(Boolean).length;
            const c = document.getElementById('swLiqCount');
            if (c) c.textContent = '流动 ' + n + ' 年';
        }
        function onLlpChange() {
            SW_PARAMS.liquid = swLiquidDefault(SW_PARAMS.llp);
            renderSwLiquidGrid();
        }

        // 取 endDate 之前（含）连续的 period 个交易日的日期列表，用于对任意基准日求移动平均
        function collectBusinessDaysBackward(endDateStr, period) {
            const out = [];
            let cur = new Date(endDateStr + 'T00:00:00Z');
            let safety = 0;
            while (out.length < period && safety < 8000) {
                const dStr = cur.toISOString().slice(0, 10);
                if (isTradingDay(dStr)) out.push(dStr);
                cur.setUTCDate(cur.getUTCDate() - 1);
                safety++;
            }
            return out.reverse();
        }

        // 求某基准日（实际日期或预测日期）的 period 日移动平均即期曲线
        // 实际日期：直接取截至该日的最后 period 个实际交易日（与 Excel 750 日移动平均一致，避免节假日被填入最新日利率而偏低）
        // 预测日期：窗口共 period 个交易日、结束于 F；未来交易日不可得，按平推法取 refOverride（默认最新实际日）即期，历史部分取实际行
        function maAsOfDate(gov, period, endDateStr, refOverride) {
            const actualDates = gov.dates;
            const dateSet = new Set(actualDates);
            const refDate = (refOverride && dateSet.has(refOverride)) ? refOverride : actualDates[actualDates.length - 1];
            const refRates = gov.rates[actualDates.indexOf(refDate)];
            const endIdx = actualDates.indexOf(endDateStr);

            let rows;
            if (endIdx >= 0) {
                // 实际日期：截至该日的最后 period 个实际交易日
                const startIdx = Math.max(0, endIdx - period + 1);
                rows = gov.rates.slice(startIdx, endIdx + 1);
            } else {
                // 预测日期：统计 (最新实际日, F] 内的交易日数 k，历史取最近 (period-k) 个实际行，尾部补 k 份平推基准即期
                const lastActual = actualDates[actualDates.length - 1];
                const k = collectBusinessDaysBackward(endDateStr, period).filter(d => d > lastActual).length;
                const histCount = Math.max(0, period - k);
                const startIdx = Math.max(0, actualDates.length - histCount);
                rows = gov.rates.slice(startIdx).concat(new Array(k).fill(refRates));
            }
            const synth = { dates: rows.map((_, i) => 'r' + i), rates: rows, latestDate: endDateStr };
            return computeMAFromStore(synth, period);
        }

        function isForecastDate(gov, d) {
            return gov && gov.dates && d && !gov.dates.includes(d) && d > gov.latestDate;
        }

        // 取某日期的前一个实际交易日（对比日默认用上一交易日）
        function getPrevTradingDay(gov, dateStr) {
            if (!gov || !gov.dates || gov.dates.length === 0) return dateStr;
            let idx = gov.dates.indexOf(dateStr);
            if (idx < 0) idx = gov.dates.length - 1; // 预测日 → 用最新实际日
            return idx > 0 ? gov.dates[idx - 1] : gov.dates[idx];
        }

        // 取所有实际日期中的每月末（每月最后一个交易日），最近在前，限 120 个
        function getMonthEndDatesFromGov(gov) {
            if (!gov || !gov.dates) return [];
            const dates = gov.dates;
            const ends = [];
            for (let i = 0; i < dates.length; i++) {
                const ym = dates[i].slice(0, 7);
                const next = (i + 1 < dates.length) ? dates[i + 1] : null;
                if (!next || next.slice(0, 7) !== ym) ends.push(dates[i]);
            }
            return ends.reverse().slice(0, 120);
        }

        // 对比日（默认上一交易日；用户可在对比图内选每月末）
        function getEffectiveCompareDate(gov, baseDate) {
            return discountCompareDate || getPrevTradingDay(gov, baseDate);
        }

        // 给定基准日（实际/预测）计算折现率曲线，供当前日与对比日共用
        function computeForDate(endDateStr) {
            const gov = (typeof dataStores !== 'undefined' && dataStores.gov_spot) ? dataStores.gov_spot : dataStore;
            if (!gov || !gov.dates || gov.dates.length === 0) return null;
            const fc = isForecastDate(gov, endDateStr);
            const ref = fc ? (discountForecastRefDate || gov.latestDate) : null;
            if (discountRule === 'alm') {
                const spot = spotAsOfDate(gov, endDateStr, ref);
                const d = computeDiscountCurvesALM(spot, endDateStr);
                d._date = endDateStr; d._ref = ref;
                return d;
            }
            if (discountRule === 'c2_new') {
                const spot = spotAsOfDate(gov, endDateStr, ref);
                const d = computeDiscountCurvesNew(spot, endDateStr, SW_PARAMS);
                d._date = endDateStr;
                d._ref = ref;
                return d;
            }
            const ma750 = maAsOfDate(gov, 750, endDateStr, ref);
            const ma60 = maAsOfDate(gov, 60, endDateStr, ref);
            const d = computeDiscountCurves(ma750, ma60, endDateStr);
            d._date = endDateStr;
            d._ref = ref;
            return d;
        }

        // === ALM（现行）折现率曲线：国债即期 + 固定溢价(70/45/30bp) ===
        const ALM_KEY_DURATIONS = [0, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50];
        const ALM_SHOCK_DECIMAL = 10 / 10000;   // 关键久期三角型冲击峰值 10bp
        const ALM_SHOCK_50BP = 0.005;           // ±50bp 平行移动
        const ALM_PREM = { high: 0.007, mid: 0.0045, low: 0.003 };  // 70 / 45 / 30 bp
        function computeDiscountCurvesALM(spot, latestDate) {
            const years = [];
            for (let t = 1; t <= 50; t++) years.push(t);
            // 连续即期曲线：整数年锚点 1..50；sub-1Y 用短端真实曲线，按 Excel「修正久期曲线」锚点法分段线性
            let shortPts = null;
            if (typeof govSpotShortStore !== 'undefined' && govSpotShortStore && govSpotShortStore.dates && govSpotShortStore.dates.length) {
                const si = govSpotShortStore.dates.indexOf(latestDate);
                if (si >= 0 && govSpotShortStore.terms && govSpotShortStore.rows && govSpotShortStore.rows[si]) {
                    const trow = govSpotShortStore.rows[si];
                    shortPts = [];
                    for (let i = 0; i < govSpotShortStore.terms.length; i++) {
                        const tt = +govSpotShortStore.terms[i];
                        const rv = trow[i];
                        if (tt < 1 && rv != null && isFinite(rv)) shortPts.push({ t: tt, r: rv / 100 });
                    }
                    shortPts.sort((a, b) => a.t - b.t);
                }
            }
            // 在短端曲线上取标准锚点值（容差匹配）
            function anchorAt(a) {
                if (!shortPts) return null;
                for (const p of shortPts) if (Math.abs(p.t - a) < 1e-4) return p.r;
                return null;
            }
            function spotAtYear(yf) {
                if (yf <= 0) {
                    if (shortPts && shortPts.length) return shortPts[0].r;
                    return (spot[1] != null && spot[2] != null) ? (2 * spot[1] - spot[2]) : (spot[1] != null ? spot[1] : null);
                }
                if (yf >= 50) return spot[50];
                if (yf < 1) {
                    if (shortPts && shortPts.length) {
                        // 与 Excel「修正久期曲线」sub-1Y 一致：标准锚点 [0,1/6,1/4,1/2,3/4,1Y] 分段线性
                        const segs = [0, 0.166667, 0.25, 0.5, 0.75, 1];
                        const vals = [shortPts[0].r, anchorAt(0.166667), anchorAt(0.25), anchorAt(0.5), anchorAt(0.75), spot[1]];
                        let lo = 0, hi = 1, vl = vals[0], vh = vals[5];
                        for (let i = 0; i < 5; i++) {
                            if (yf >= segs[i] - 1e-7 && yf <= segs[i + 1] + 1e-7) { lo = segs[i]; hi = segs[i + 1]; vl = vals[i]; vh = vals[i + 1]; break; }
                        }
                        if (lo === hi) return vl;
                        if (vl == null || vh == null) {
                            let chosen = shortPts[0];
                            for (let i = 0; i < shortPts.length; i++) { if (shortPts[i].t <= yf + 1e-6) chosen = shortPts[i]; else break; }
                            return chosen.r;
                        }
                        return vl + (vh - vl) * (yf - lo) / (hi - lo);
                    }
                    const y0 = (spot[1] != null && spot[2] != null) ? (2 * spot[1] - spot[2]) : spot[1];
                    return (y0 != null && spot[1] != null) ? (y0 + (spot[1] - y0) * yf) : null;
                }
                const lo = Math.floor(yf), hi = Math.ceil(yf);
                if (lo === hi) return spot[lo];
                const f = spot[lo], g = spot[hi];
                if (f == null || g == null) return null;
                return f + (g - f) * (yf - lo);
            }
            const high = {}, mid = {}, low = {}, fhigh = {}, fmid = {}, flow = {};
            years.forEach(t => {
                const s = spot[t];
                if (s == null) return;
                high[t] = +(s + ALM_PREM.high).toFixed(6);
                mid[t]  = +(s + ALM_PREM.mid).toFixed(6);
                low[t]  = +(s + ALM_PREM.low).toFixed(6);
            });
            years.forEach(t => {
                if (t === 1) { fhigh[t] = high[1]; fmid[t] = mid[1]; flow[t] = low[1]; }
                else if (high[t] != null && high[t - 1] != null) {
                    fhigh[t] = +(Math.pow(1 + high[t], t) / Math.pow(1 + high[t - 1], t - 1) - 1).toFixed(6);
                    fmid[t]  = +(Math.pow(1 + mid[t], t) / Math.pow(1 + mid[t - 1], t - 1) - 1).toFixed(6);
                    flow[t]  = +(Math.pow(1 + low[t], t) / Math.pow(1 + low[t - 1], t - 1) - 1).toFixed(6);
                }
            });
            // 月度（0..600 月）：base = 国债即期在 yearFrac=m/12 处线性插值（与 Excel 修正久期曲线一致）
            const NMON = 601;
            const mhigh = new Array(NMON), mmid = new Array(NMON), mlow = new Array(NMON);
            for (let m = 0; m < NMON; m++) {
                const base = spotAtYear(m / 12);
                if (base == null) { mhigh[m] = mmid[m] = mlow[m] = null; continue; }
                mhigh[m] = +(base + ALM_PREM.high).toFixed(6);
                mmid[m]  = +(base + ALM_PREM.mid).toFixed(6);
                mlow[m]  = +(base + ALM_PREM.low).toFixed(6);
            }
            return {
                years, months: NMON,
                liab: {
                    spot: { high, mid, low },
                    fwd: { high: fhigh, mid: fmid, low: flow },
                    monthly: { high: mhigh, mid: mmid, low: mlow }
                },
                latestDate, _key: null, _date: latestDate, _ref: null
            };
        }
        function almKeyDurImpact(kd, yearFrac) {
            let prevKd = null, nextKd = null;
            for (let i = 0; i < ALM_KEY_DURATIONS.length; i++) {
                const k = ALM_KEY_DURATIONS[i];
                if (k < kd) prevKd = k;
                if (k > kd && nextKd === null) { nextKd = k; break; }
            }
            if (Math.abs(yearFrac - kd) < 1e-10) return ALM_SHOCK_DECIMAL;
            if (prevKd !== null && prevKd <= yearFrac && yearFrac < kd) return ALM_SHOCK_DECIMAL * (yearFrac - prevKd) / (kd - prevKd);
            if (nextKd !== null && kd < yearFrac && yearFrac <= nextKd) return ALM_SHOCK_DECIMAL * (nextKd - yearFrac) / (nextKd - kd);
            return 0;
        }

        function ensureDiscountData() {
            const gov = (typeof dataStores !== 'undefined' && dataStores.gov_spot) ? dataStores.gov_spot : dataStore;
            if (!gov || !gov.dates || gov.dates.length === 0) return null;
            const endDate = discountDate || gov.latestDate;
            const fc = isForecastDate(gov, endDate);
            const ref = fc ? (discountForecastRefDate || gov.latestDate) : null;
            const cacheKey = endDate + '|' + (ref || '');
            if (!discountDataCache || discountDataCache._key !== cacheKey) {
                discountDataCache = computeForDate(endDate);
                discountDataCache._key = cacheKey;
            }
            return discountDataCache;
        }

        function discGetGov() {
            return (typeof dataStores !== 'undefined' && dataStores.gov_spot) ? dataStores.gov_spot : dataStore;
        }

        function populateDiscountDateSelect() {
            const gov = discGetGov();
            if (!gov || !gov.dates || gov.dates.length === 0) return;
            // 实际日期按 年->月->日 分组，便于分级定位（早年份也易找）
            const byYM = {};
            gov.dates.forEach(d => {
                const ym = d.slice(0, 7);
                (byYM[ym] = byYM[ym] || new Set()).add(d.slice(8));
            });
            const years = [...new Set(gov.dates.map(d => d.slice(0, 4)))].sort().reverse();
            discountYMD = { byYM, years };
            // 预测月未日期
            const fDates = generateForecastDates(gov.latestDate, 750);
            discountForecastList = getMonthEndDates(fDates);
            const fsel = document.getElementById('discountForecastDateSelect');
            if (fsel) fsel.innerHTML = discountForecastList.map(d => `<option value="${d}">${d} (预测)</option>`).join('');
            syncDiscountDatePickerMode(gov);
            discountDatePopulated = true;
            // 对比日下拉：默认「上一交易日」，可选每月末（不列全部日期）
            const csel = document.getElementById('discountCompareDateSelect');
            if (csel) {
                const me = getMonthEndDatesFromGov(gov);
                const opts = ['<option value="">（上一交易日）</option>']
                    .concat(me.map(d => `<option value="${d}" ${d === discountCompareDate ? 'selected' : ''}>${d}</option>`));
                csel.innerHTML = opts.join('');
            }
        }

        // 实际/预测 两种模式切换，并据 discountDate 同步控件显示
        function syncDiscountDatePickerMode(gov) {
            const isFc = discountDate && isForecastDate(gov, discountDate);
            const ymdWrap = document.getElementById('discountYMDWrap');
            const fsel = document.getElementById('discountForecastDateSelect');
            const btn = document.getElementById('discountToggleFc');
            if (isFc) {
                if (ymdWrap) ymdWrap.style.display = 'none';
                if (fsel) { fsel.style.display = ''; fsel.value = discountDate; }
                if (btn) btn.textContent = '实际';
            } else {
                if (ymdWrap) ymdWrap.style.display = 'inline-flex';
                if (fsel) fsel.style.display = 'none';
                if (btn) btn.textContent = '预测';
                syncDiscountYMDFromDate(gov, discountDate || gov.latestDate);
            }
        }

        function syncDiscountYMDFromDate(gov, date) {
            const ySel = document.getElementById('discountYearSel');
            const mSel = document.getElementById('discountMonthSel');
            const dSel = document.getElementById('discountDaySel');
            if (!ySel || !mSel || !dSel || !discountYMD) return;
            const y = date.slice(0, 4), m = date.slice(5, 7), d = date.slice(8);
            ySel.innerHTML = discountYMD.years.map(yy => `<option value="${yy}" ${yy === y ? 'selected' : ''}>${yy}</option>`).join('');
            populateDiscountMonths(y, m);
            populateDiscountDays(y, m, d);
        }

        function populateDiscountMonths(year, selMonth) {
            const mSel = document.getElementById('discountMonthSel');
            if (!mSel || !discountYMD) return;
            const months = [...new Set(Object.keys(discountYMD.byYM).filter(k => k.slice(0, 4) === year).map(k => k.slice(5)))].sort();
            mSel.innerHTML = months.map(mm => `<option value="${mm}" ${mm === selMonth ? 'selected' : ''}>${mm}</option>`).join('');
        }

        function populateDiscountDays(year, month, selDay) {
            const dSel = document.getElementById('discountDaySel');
            if (!dSel || !discountYMD) return;
            const days = [...(discountYMD.byYM[year + '-' + month] || [])].sort();
            dSel.innerHTML = days.map(dd => `<option value="${dd}" ${dd === selDay ? 'selected' : ''}>${dd}</option>`).join('');
        }

        function getCurrentDiscountDate() {
            const ySel = document.getElementById('discountYearSel');
            const mSel = document.getElementById('discountMonthSel');
            const dSel = document.getElementById('discountDaySel');
            if (ySel && mSel && dSel && ySel.value && mSel.value && dSel.value)
                return ySel.value + '-' + mSel.value + '-' + dSel.value;
            return null;
        }

        function onDiscountDateChanged(val) {
            if (!val) return;
            discountDate = val;
            discountDataCache = null;
            syncDiscountForecastRefUI();
            renderDiscount();
        }

        function ruleLabel() {
            if (discountRule === 'c2_new') return 'Solvency II';
            if (discountRule === 'alm') return 'ALM（现行）';
            return 'CROSS';
        }
        function renderDiscount() {
            if (discountRule === 'c2_new') syncSWParamsFromInputs();
            const d = ensureDiscountData();
            if (!d) { alert('国债即期数据尚未加载，无法生成折现率曲线'); return; }
            const gov = (typeof dataStores !== 'undefined' && dataStores.gov_spot) ? dataStores.gov_spot : dataStore;
            const isForecast = isForecastDate(gov, d.latestDate);
            const dateBadge = document.getElementById('discountDateBadge');
            if (dateBadge) {
                dateBadge.textContent = (isForecast ? '预测 · ' : '实际 · ') + d.latestDate;
                dateBadge.classList.toggle('forecast', !!isForecast);
            }
            const refTxt = (isForecast && d._ref) ? ' ｜ 平推基准 ' + d._ref : '';
            const basisTxt = discountRule === 'c2_new'
                ? '国债即期当日 · Smith-Wilson'
                : '负债=MA750 · 利率=MA60';
            document.getElementById('discountSummaryBadge').textContent =
                '基准日 ' + d.latestDate + (isForecast ? '（预测）' : '') + refTxt + ' ｜ ' + basisTxt;
            const cmpBadge = document.getElementById('discountCompareBadge');
            if (cmpBadge) {
                const cmpDate = getEffectiveCompareDate(gov, d.latestDate);
                const cfc = isForecastDate(gov, cmpDate);
                cmpBadge.textContent = '对比日 ' + cmpDate + (discountCompareDate ? '' : '（上一交易日）') + (cfc ? '（预测）' : '') + ' ｜ 负债中档 · 利率上升/下降';
                cmpBadge.classList.toggle('forecast', !!cfc);
            }
            // 切换汇总/输出标题的基准日标注
            const outTitleEl = document.getElementById('discountOutputTitle');
            if (outTitleEl) outTitleEl.textContent = '假设表（DISC_PC）· 基准日 ' + d.latestDate + (isForecast ? '（预测）' : '') + refTxt;
            renderDiscountSummary(discountSF);
        }

        function populateDiscountForecastRefSelect(gov) {
            const wrap = document.getElementById('discountForecastRefWrap');
            const sel = document.getElementById('discountForecastRefSelect');
            if (!wrap || !sel) return;
            const actualDates = gov.dates;
            // 提供最近 120 个实际交易日供选择，默认最新实际日
            const recent = actualDates.slice(-120);
            sel.innerHTML = recent.map(d => `<option value="${d}" ${d === (discountForecastRefDate || gov.latestDate) ? 'selected' : ''}>${d}</option>`).join('');
            wrap.style.display = '';
        }

        function syncDiscountForecastRefUI() {
            const gov = (typeof dataStores !== 'undefined' && dataStores.gov_spot) ? dataStores.gov_spot : dataStore;
            if (!gov) return;
            const wrap = document.getElementById('discountForecastRefWrap');
            if (!wrap) return;
            if (isForecastDate(gov, discountDate)) {
                if (!discountForecastRefDate) discountForecastRefDate = gov.latestDate;
                populateDiscountForecastRefSelect(gov);
            } else {
                wrap.style.display = 'none';
                discountForecastRefDate = null;
            }
        }

        function renderDiscountSummary(mode) {
            const d = discountDataCache;
            const gov = (typeof dataStores !== 'undefined' && dataStores.gov_spot) ? dataStores.gov_spot : dataStore;
            const years = d.years;
            const liabSpot = d.liab.spot, liabFwd = d.liab.fwd;
            const rateSpot = (d.rate && d.rate.spot) || {}, rateFwd = (d.rate && d.rate.fwd) || {};
            const groupsAll = (discountRule === 'alm') ? [
                { name:'负债即期-高', kind:'spot', data: liabSpot.high, color:'#c0392b', dash:false },
                { name:'负债即期-中', kind:'spot', data: liabSpot.mid, color:'#e67e22', dash:false },
                { name:'负债即期-低', kind:'spot', data: liabSpot.low, color:'#d4ac0d', dash:false },
                { name:'负债远期-高', kind:'fwd', data: liabFwd.high, color:'#c0392b', dash:true },
                { name:'负债远期-中', kind:'fwd', data: liabFwd.mid, color:'#e67e22', dash:true },
                { name:'负债远期-低', kind:'fwd', data: liabFwd.low, color:'#d4ac0d', dash:true }
            ] : [
                { name:'负债即期-高', kind:'spot', data: liabSpot.high, color:'#c0392b', dash:false },
                { name:'负债即期-中', kind:'spot', data: liabSpot.mid, color:'#e67e22', dash:false },
                { name:'负债即期-低', kind:'spot', data: liabSpot.low, color:'#d4ac0d', dash:false },
                { name:'负债远期-高', kind:'fwd', data: liabFwd.high, color:'#c0392b', dash:true },
                { name:'负债远期-中', kind:'fwd', data: liabFwd.mid, color:'#e67e22', dash:true },
                { name:'负债远期-低', kind:'fwd', data: liabFwd.low, color:'#d4ac0d', dash:true },
                { name:'利率即期-基础', kind:'spot', data: rateSpot.base, color:'#2980b9', dash:false },
                { name:'利率即期-上升', kind:'spot', data: rateSpot.up, color:'#8e44ad', dash:false },
                { name:'利率即期-下降', kind:'spot', data: rateSpot.down, color:'#16a085', dash:false },
                { name:'利率远期-基础', kind:'fwd', data: rateFwd.base, color:'#2980b9', dash:true },
                { name:'利率远期-上升', kind:'fwd', data: rateFwd.up, color:'#8e44ad', dash:true },
                { name:'利率远期-下降', kind:'fwd', data: rateFwd.down, color:'#16a085', dash:true }
            ];
            const groups = groupsAll.filter(g => (mode === 'fwd' ? g.kind === 'fwd' : g.kind === 'spot'));
            const series = groups.map(g => ({
                name: g.name, type: 'line', showSymbol: false,
                data: years.map(t => g.data[t] == null ? null : +(g.data[t] * 100).toFixed(4)),
                lineStyle: { width: 1.6, type: g.dash ? 'dashed' : 'solid', color: g.color },
                itemStyle: { color: g.color }, emphasis: { focus: 'series' }
            }));
            const chartDom = document.getElementById('discountSummaryChart');
            if (!discountChart) discountChart = echarts.init(chartDom);
            discountChart.setOption({
                title: { text: '折现率曲线（' + (mode === 'fwd' ? '远期' : '即期') + '）· ' + ruleLabel(), left:'center', textStyle:{fontSize:14} },
                tooltip: { trigger:'axis' },
                legend: { type:'scroll', bottom: 0, textStyle:{fontSize:11} },
                grid: { left: 52, right: 22, top: 42, bottom: 62 },
                xAxis: { type:'category', data: years, name:'年', nameLocation:'middle', nameGap: 28 },
                yAxis: { type:'value', name:'%', axisLabel:{ formatter: v => v.toFixed(2) } },
                series: series
            }, true);
            // 右图：曲线对比（当前日 vs 对比日），仅负债中档 + 利率基础
            const cmpDom = document.getElementById('discountCompareChart');
            if (cmpDom) {
                const cmpDate = getEffectiveCompareDate(gov, d.latestDate);
                const cd = computeForDate(cmpDate);
                if (!discountCompareChart) discountCompareChart = echarts.init(cmpDom);
                const curMid = d.liab.spot.mid, curUp = d.rate ? d.rate.spot.up : null, curDown = d.rate ? d.rate.spot.down : null;
                const curMidF = d.liab.fwd.mid, curUpF = d.rate ? d.rate.fwd.up : null, curDownF = d.rate ? d.rate.fwd.down : null;
                const cmpMid = cd.liab.spot.mid, cmpUp = cd.rate ? cd.rate.spot.up : null, cmpDown = cd.rate ? cd.rate.spot.down : null;
                const cmpMidF = cd.liab.fwd.mid, cmpUpF = cd.rate ? cd.rate.fwd.up : null, cmpDownF = cd.rate ? cd.rate.fwd.down : null;
                const cmpAll = [
                    { name:'当前·负债中档', kind:'spot', data: curMid, color:'#e67e22', dash:false },
                    { name:'对比·负债中档', kind:'spot', data: cmpMid, color:'#e67e22', dash:true },
                    { name:'当前·利率上升', kind:'spot', data: curUp, color:'#8e44ad', dash:false },
                    { name:'对比·利率上升', kind:'spot', data: cmpUp, color:'#8e44ad', dash:true },
                    { name:'当前·利率下降', kind:'spot', data: curDown, color:'#16a085', dash:false },
                    { name:'对比·利率下降', kind:'spot', data: cmpDown, color:'#16a085', dash:true },
                    { name:'当前·负债中档', kind:'fwd', data: curMidF, color:'#e67e22', dash:false },
                    { name:'对比·负债中档', kind:'fwd', data: cmpMidF, color:'#e67e22', dash:true },
                    { name:'当前·利率上升', kind:'fwd', data: curUpF, color:'#8e44ad', dash:false },
                    { name:'对比·利率上升', kind:'fwd', data: cmpUpF, color:'#8e44ad', dash:true },
                    { name:'当前·利率下降', kind:'fwd', data: curDownF, color:'#16a085', dash:false },
                    { name:'对比·利率下降', kind:'fwd', data: curDownF, color:'#16a085', dash:true }
                ];
                const cmpGroups = cmpAll.filter(g => g.data && (mode === 'fwd' ? g.kind === 'fwd' : g.kind === 'spot'));
                const cmpSeries = cmpGroups.map(g => ({
                    name: g.name, type:'line', showSymbol:false,
                    data: years.map(t => g.data[t] == null ? null : +(g.data[t]*100).toFixed(4)),
                    lineStyle:{ width:1.8, type: g.dash?'dashed':'solid', color:g.color },
                    itemStyle:{ color:g.color }, emphasis:{ focus:'series' }
                }));
                console.log('[discountCompare] cmpDate=', cmpDate, 'mode=', mode, 'seriesCount=', cmpSeries.length, 'firstSeriesSample=', cmpSeries[0] && cmpSeries[0].data.slice(0,5));
                discountCompareChart.setOption({
                    title:{ text:'曲线对比 · 当前 '+d.latestDate+' vs '+cmpDate, left:'center', textStyle:{fontSize:14} },
                    tooltip:{ trigger:'axis' },
                    legend:{ type:'scroll', bottom:0, textStyle:{fontSize:11} },
                    grid:{ left:52, right:22, top:42, bottom:62 },
                    xAxis:{ type:'category', data:years, name:'年', nameLocation:'middle', nameGap:28 },
                    yAxis:{ type:'value', name:'%', axisLabel:{ formatter: v=>v.toFixed(2) } },
                    series: cmpSeries
                }, true);
                // 确保图表在 grid/flex 布局完成后再计算尺寸
                if (discountCompareChart) {
                    discountCompareChart.resize();
                    setTimeout(() => discountCompareChart.resize(), 0);
                    setTimeout(() => discountCompareChart.resize(), 100);
                }
            }
            const thead = document.getElementById('discountSummaryHead');
            const tbody = document.getElementById('discountSummaryBody');
            let h = '<tr><th>年</th>';
            groups.forEach(g => h += '<th>' + g.name + '</th>');
            h += '</tr>';
            thead.innerHTML = h;
            let b = '';
            for (const t of years) {
                b += '<tr><td><strong>' + t + '</strong></td>';
                groups.forEach(g => { const v = g.data[t]; b += '<td>' + (v == null ? '—' : (v * 100).toFixed(4)) + '</td>'; });
                b += '</tr>';
            }
            tbody.innerHTML = b;
            document.getElementById('discountSummaryTitle').textContent = '负债折现率曲线（汇总 · ' + (mode === 'fwd' ? '远期' : '即期') + '）';
        }

        const DISCOUNT_PRODUCTS = [
            { seq:1, cat:'C', cr:'mid', up:'mid', dw:'mid' },
            { seq:2, cat:'C', cr:'mid', up:'mid', dw:'mid' },
            { seq:3, cat:'U万能/投连', cr:'low', up:'low', dw:'low' },
            { seq:4, cat:'U万能/投连', cr:'low', up:'low', dw:'low' },
            { seq:5, cat:'U万能/投连', cr:'low', up:'low', dw:'low' },
            { seq:6, cat:'C高现价/中短存', cr:'low', up:'low', dw:'low' },
            { seq:7, cat:'U万能/投连', cr:'low', up:'low', dw:'low' },
            { seq:9, cat:'C', cr:'mid', up:'mid', dw:'mid' },
            { seq:10, cat:'U万能/投连', cr:'low', up:'low', dw:'low' },
            { seq:11, cat:'C', cr:'mid', up:'mid', dw:'mid' }
        ];

        function renderDiscountOutput(mode) {
            if (discountRule === 'alm') { renderDiscountOutputALM(mode); return; }
            const d = discountDataCache;
            const years = d.years;
            const liabSpot = d.liab.spot, liabFwd = d.liab.fwd;
            const rateSpot = d.rate.spot, rateFwd = d.rate.fwd;
            // 四个板块（DISC_PC_CR / UP / DW / BASE），与 Excel「输出」表一致；cr 档='middle'
            const blocks = [
                { name:'负债基本情景曲线DISC_PC_CR', idx:1, cat:true,  get:p => (mode==='fwd'?liabFwd:liabSpot)[p.cr],            lvl:p => p.cr==='mid' ? 'middle' : 'low' },
                { name:'利率风险向上曲线 DISC_PC_UP 当CRS_IND=2时为60日曲线，否则为750日曲线', idx:2, cat:false, get:p => (mode==='fwd'?rateFwd.up:rateSpot.up),    lvl:p => p.up==='mid' ? 'middle+' : 'low+' },
                { name:'利率风险向下曲线 DISC_PC_DW 当CRS_IND=2时为60日曲线，否则为750日曲线', idx:3, cat:false, get:p => (mode==='fwd'?rateFwd.down:rateSpot.down), lvl:p => p.dw==='mid' ? 'middle-' : 'low-' },
                { name:'二期下计算利率风险基本情景曲线DISC_PC_BASE', idx:4, cat:true, get:p => (mode==='fwd'?rateFwd.base:rateSpot.base), lvl:p => p.cr==='mid' ? 'middle' : 'low' },
            ];
            const thead = document.getElementById('discountOutputHead');
            const tbody = document.getElementById('discountOutputBody');
            const badge = document.getElementById('discountOutputBadge');
            if (badge) badge.textContent = '产品序号 × 情景曲线 · 年1-' + years.length + ' · 远期折现率';
            let h = '<tr><th>曲线类型</th><th>情景序号</th><th>产品类别</th><th>产品序号</th><th>情景档</th>';
            years.forEach(t => h += '<th>' + t + '</th>');
            h += '</tr>';
            thead.innerHTML = h;
            let b = '';
            for (const blk of blocks) {
                DISCOUNT_PRODUCTS.forEach((p, i) => {
                    const arr = blk.get(p);
                    const curveCell = i === 0 ? blk.name : '';
                    const catCell = blk.cat ? p.cat : '';
                    b += '<tr><td>' + curveCell + '</td><td>' + blk.idx + '</td><td>' + catCell + '</td><td>' + p.seq + '</td><td>' + blk.lvl(p) + '</td>';
                    years.forEach(t => { const v = arr[t]; b += '<td>' + (v == null ? '—' : (v * 100).toFixed(4)) + '</td>'; });
                    b += '</tr>';
                });
            }
            tbody.innerHTML = b;
        }

        function renderDiscountOutputALM(mode) {
            const d = discountDataCache;
            const months = d.months;
            const high = d.liab.monthly.high, mid = d.liab.monthly.mid, low = d.liab.monthly.low;
            const thead = document.getElementById('discountOutputHead');
            const tbody = document.getElementById('discountOutputBody');
            const badge = document.getElementById('discountOutputBadge');
            if (badge) badge.textContent = 'ALM · 关键久期 · 月度 0–600月（共' + months + '行）· ' + (mode === 'fwd' ? '远期' : '即期') + '折现率';
            let h = '<tr><th>年</th><th>月</th><th>高</th><th>中</th><th>低</th>';
            h += '<th>高+50</th><th>高-50</th><th>中+50</th><th>中-50</th><th>低+50</th><th>低-50</th>';
            ['高','中','低'].forEach(g => ALM_KEY_DURATIONS.forEach(kd => { h += '<th>' + g + '·' + kd + '+</th><th>' + g + '·' + kd + '-</th>'; }));
            h += '</tr>';
            thead.innerHTML = h;
            let b = '';
            for (let m = 0; m < months; m++) {
                const hi = high[m], mi = mid[m], lo = low[m];
                const yr = Math.floor((m - 1) / 12) + 1;
                b += '<tr><td>' + yr + '</td><td><strong>' + m + '</strong></td>';
                b += '<td>' + (hi==null?'—':(hi*100).toFixed(4)) + '</td>';
                b += '<td>' + (mi==null?'—':(mi*100).toFixed(4)) + '</td>';
                b += '<td>' + (lo==null?'—':(lo*100).toFixed(4)) + '</td>';
                b += '<td>' + (hi==null?'—':((hi+ALM_SHOCK_50BP)*100).toFixed(4)) + '</td>';
                b += '<td>' + (hi==null?'—':((hi-ALM_SHOCK_50BP)*100).toFixed(4)) + '</td>';
                b += '<td>' + (mi==null?'—':((mi+ALM_SHOCK_50BP)*100).toFixed(4)) + '</td>';
                b += '<td>' + (mi==null?'—':((mi-ALM_SHOCK_50BP)*100).toFixed(4)) + '</td>';
                b += '<td>' + (lo==null?'—':((lo+ALM_SHOCK_50BP)*100).toFixed(4)) + '</td>';
                b += '<td>' + (lo==null?'—':((lo-ALM_SHOCK_50BP)*100).toFixed(4)) + '</td>';
                [hi, mi, lo].forEach(base => {
                    ALM_KEY_DURATIONS.forEach(kd => {
                        const imp = almKeyDurImpact(kd, m / 12);
                        b += '<td>' + (base==null?'—':((base+imp)*100).toFixed(4)) + '</td>';
                        b += '<td>' + (base==null?'—':((base-imp)*100).toFixed(4)) + '</td>';
                    });
                });
                b += '</tr>';
            }
            tbody.innerHTML = b;
        }

        function pct(x) { return x == null ? null : +(x * 100).toFixed(4); }
        function discountXLSXName(suffix) {
            const d = discountDataCache;
            return '折现率曲线_' + ruleLabel() + '_' + suffix + '_基准日' + (d ? d.latestDate : '') + '.xlsx';
        }
        // 参数设置 sheet：记录本次生成所用参数（Solvency II 不含流动性标记 年度1–50；CROSS 同理）
        function discountParamsAOA(d) {
            const rows = [];
            const pct = v => (v == null) ? '' : (v * 100).toFixed(4) + '%';
            if (discountRule === 'alm') {
                rows.push(['参数设置（ALM · 现行保险资产负债管理监管规则第4号）']);
                rows.push(['基准日', d.latestDate]);
                rows.push([]);
                rows.push(['折现率生成', '国债即期收益率曲线 + 固定溢价']);
                rows.push(['溢价-高 (BP)', 70]);
                rows.push(['溢价-中 (BP)', 45]);
                rows.push(['溢价-低 (BP)', 30]);
                rows.push([]);
                rows.push(['关键久期冲击', '三角型关键利率冲击，峰值 10bp，相邻关键久期间线性归零']);
                rows.push(['关键久期集合', ALM_KEY_DURATIONS.join(', ')]);
                rows.push(['±50bp 平行移动', '用于利率风险情景列']);
                rows.push([]);
                rows.push(['注：ALM 仅用国债即期 + 固定溢价，无流动性标记。']);
                return rows;
            }
            if (discountRule === 'c2_new') {
                const P = SW_PARAMS;
                rows.push(['参数设置（Solvency II · Smith-Wilson）']);
                rows.push(['基准日', d.latestDate]);
                rows.push([]);
                rows.push(['① 基础参数', '']);
                rows.push(['UFR 基础', P.ufrBase, pct(P.ufrBase)]);
                rows.push(['α 下限', P.alphaMin]);
                rows.push(['LLP 最后流动点（年）', P.llp]);
                rows.push(['CP 收敛年', P.cp]);
                rows.push(['收敛容差 (bp)', +(P.bandwidth * 10000).toFixed(4)]);
                rows.push([]);
                rows.push(['② UFR 不利情景（上升 / 下降自动计算）', '']);
                rows.push(['利率上升·40年不利幅度', P.upMag40, pct(P.upMag40)]);
                rows.push(['利率下降·40年不利幅度', P.downMag40, pct(P.downMag40)]);
                rows.push(['UFR 上升（自动）', P.ufrUp, pct(P.ufrUp)]);
                rows.push(['UFR 下降（自动）', P.ufrDown, pct(P.ufrDown)]);
                rows.push([]);
                rows.push(['③ 综合溢价 (BP)', '']);
                rows.push(['负债溢价-高', +(P.premHigh * 10000).toFixed(2)]);
                rows.push(['负债溢价-中', +(P.premMid * 10000).toFixed(2)]);
                rows.push(['负债溢价-低', +(P.premLow * 10000).toFixed(2)]);
                rows.push(['终极利率溢价 (VA)', +(P.ultPrem * 10000).toFixed(2)]);
                rows.push([]);
                rows.push(['收敛 α（确定性结果，非输入项）', '']);
                rows.push(['负债曲线 α（中档）', d.liab._extr ? d.liab._extr[1] : null]);
                rows.push(['利率曲线 α（基础）', d.rate._extr && d.rate._extr.base ? d.rate._extr.base[1] : null]);
                rows.push(['利率曲线 α（上升）', d.rate._extr && d.rate._extr.up ? d.rate._extr.up[1] : null]);
                rows.push(['利率曲线 α（下降）', d.rate._extr && d.rate._extr.down ? d.rate._extr.down[1] : null]);
                rows.push([]);
                rows.push(['注：流动性标记（年度 1–50）未列入本表，可在网页参数面板查看 / 调整。']);
            } else {
                const P = DISC_PARAMS;
                rows.push(['参数设置（CROSS · 偿二代二期）']);
                rows.push(['基准日', d.latestDate]);
                rows.push([]);
                rows.push(['基础参数', '']);
                rows.push(['起始年 (startYear)', P.startYear]);
                rows.push(['终止年 (endYear)', P.endYear]);
                rows.push(['UFR 基础', P.ufr, pct(P.ufr)]);
                rows.push(['UFR 上升', P.ufrUp, pct(P.ufrUp)]);
                rows.push(['UFR 下降', P.ufrDown, pct(P.ufrDown)]);
                rows.push([]);
                rows.push(['综合溢价 (BP)', '']);
                rows.push(['负债溢价-高', +(P.spreadHigh * 10000).toFixed(2)]);
                rows.push(['负债溢价-中', +(P.spreadMid * 10000).toFixed(2)]);
                rows.push(['负债溢价-低', +(P.spreadLow * 10000).toFixed(2)]);
                rows.push(['利率曲线溢价 (Int)', +(P.spreadInt * 10000).toFixed(2)]);
                rows.push([]);
                rows.push(['压力参数：见网页「利率风险情景」说明表（年度 1–50 上升 / 下降）。']);
            }
            return rows;
        }
        function appendDiscountParamsSheet(wb, d) {
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(discountParamsAOA(d)), '参数设置');
        }
        function downloadDiscountSummaryXLSX() {
            const d = ensureDiscountData(); if (!d) return;
            if (discountRule === 'alm') {
                const years = d.years;
                const groups = [
                    ['负债即期-高', d.liab.spot.high], ['负债即期-中', d.liab.spot.mid], ['负债即期-低', d.liab.spot.low],
                    ['负债远期-高', d.liab.fwd.high], ['负债远期-中', d.liab.fwd.mid], ['负债远期-低', d.liab.fwd.low]
                ];
                const aoa = [['年'].concat(groups.map(g => g[0]))];
                years.forEach(t => {
                    const row = [t];
                    groups.forEach(g => row.push(g[1][t] == null ? null : +(g[1][t] * 100).toFixed(4)));
                    aoa.push(row);
                });
                const wb = XLSX.utils.book_new();
                XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), '汇总');
                appendDiscountParamsSheet(wb, d);
                XLSX.writeFile(wb, discountXLSXName('汇总'));
                return;
            }
            const years = d.years;
            const groups = [
                ['负债即期-高', d.liab.spot.high], ['负债即期-中', d.liab.spot.mid], ['负债即期-低', d.liab.spot.low],
                ['负债远期-高', d.liab.fwd.high], ['负债远期-中', d.liab.fwd.mid], ['负债远期-低', d.liab.fwd.low],
                ['利率即期-基础', d.rate.spot.base], ['利率即期-上升', d.rate.spot.up], ['利率即期-下降', d.rate.spot.down],
                ['利率远期-基础', d.rate.fwd.base], ['利率远期-上升', d.rate.fwd.up], ['利率远期-下降', d.rate.fwd.down]
            ];
            const aoa = [['年'].concat(groups.map(g => g[0]))];
            for (const t of years) {
                const row = [t];
                groups.forEach(g => row.push(g[1][t] == null ? null : +(g[1][t] * 100).toFixed(4)));
                aoa.push(row);
            }
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), '汇总');
            appendDiscountParamsSheet(wb, d);
            XLSX.writeFile(wb, discountXLSXName('汇总'));
        }
        function downloadDiscountOutputXLSX() {
            const d = ensureDiscountData(); if (!d) return;
            if (discountRule === 'alm') {
                const months = d.months;
                const high = d.liab.monthly.high, mid = d.liab.monthly.mid, low = d.liab.monthly.low;
                const aoa = [];
                const r1 = new Array(131).fill('');
                r1[5] = 'High'; r1[7] = 'Middle'; r1[9] = 'Low'; r1[11] = 'High'; r1[51] = 'Middle'; r1[91] = 'Low';
                aoa.push(r1);
                const r2 = new Array(131).fill('');
                r2[0] = '负债即期利率曲线';
                r2[2] = 'High'; r2[3] = 'Middle'; r2[4] = 'Low';
                const sub = ['+50bps', '-50bps', '+50bps', '-50bps', '+50bps', '-50bps'];
                for (let i = 0; i < 6; i++) r2[5 + i] = sub[i];
                let col = 11;
                ['高', '中', '低'].forEach(() => { ALM_KEY_DURATIONS.forEach(kd => { r2[col] = kd + '+'; col++; r2[col] = kd + '-'; col++; }); });
                aoa.push(r2);
                const r3 = new Array(131).fill('');
                r3[0] = 'year'; r3[1] = 'month';
                for (let c = 3; c < 132; c++) r3[c - 1] = c - 3;
                aoa.push(r3);
                for (let m = 0; m < months; m++) {
                    const hi = high[m], mi = mid[m], lo = low[m];
                    const yr = Math.floor((m - 1) / 12) + 1;
                    const row = new Array(131).fill('');
                    row[0] = yr; row[1] = m;
                    row[2] = hi == null ? null : +(hi * 100).toFixed(6);
                    row[3] = mi == null ? null : +(mi * 100).toFixed(6);
                    row[4] = lo == null ? null : +(lo * 100).toFixed(6);
                    row[5] = hi == null ? null : +((hi + ALM_SHOCK_50BP) * 100).toFixed(6);
                    row[6] = hi == null ? null : +((hi - ALM_SHOCK_50BP) * 100).toFixed(6);
                    row[7] = mi == null ? null : +((mi + ALM_SHOCK_50BP) * 100).toFixed(6);
                    row[8] = mi == null ? null : +((mi - ALM_SHOCK_50BP) * 100).toFixed(6);
                    row[9] = lo == null ? null : +((lo + ALM_SHOCK_50BP) * 100).toFixed(6);
                    row[10] = lo == null ? null : +((lo - ALM_SHOCK_50BP) * 100).toFixed(6);
                    let c2 = 11;
                    [hi, mi, lo].forEach(base => {
                        ALM_KEY_DURATIONS.forEach(kd => {
                            const imp = almKeyDurImpact(kd, m / 12);
                            row[c2] = base == null ? null : +((base + imp) * 100).toFixed(6); c2++;
                            row[c2] = base == null ? null : +((base - imp) * 100).toFixed(6); c2++;
                        });
                    });
                    aoa.push(row);
                }
                const wb = XLSX.utils.book_new();
                XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), 'Liability_Output');
                appendDiscountParamsSheet(wb, d);
                XLSX.writeFile(wb, discountXLSXName('输出'));
                return;
            }
            const years = d.years;
            const liabSpot = d.liab.spot, liabFwd = d.liab.fwd, rateSpot = d.rate.spot, rateFwd = d.rate.fwd;
            const blocks = [
                { name:'负债基本情景曲线DISC_PC_CR', idx:1, cat:true,  get:p => (discountSF==='fwd'?liabFwd:liabSpot)[p.cr],            lvl:p => p.cr==='mid' ? 'middle' : 'low' },
                { name:'利率风险向上曲线 DISC_PC_UP 当CRS_IND=2时为60日曲线，否则为750日曲线', idx:2, cat:false, get:p => (discountSF==='fwd'?rateFwd.up:rateSpot.up),    lvl:p => p.up==='mid' ? 'middle+' : 'low+' },
                { name:'利率风险向下曲线 DISC_PC_DW 当CRS_IND=2时为60日曲线，否则为750日曲线', idx:3, cat:false, get:p => (discountSF==='fwd'?rateFwd.down:rateSpot.down), lvl:p => p.dw==='mid' ? 'middle-' : 'low-' },
                { name:'二期下计算利率风险基本情景曲线DISC_PC_BASE', idx:4, cat:true, get:p => (discountSF==='fwd'?rateFwd.base:rateSpot.base), lvl:p => p.cr==='mid' ? 'middle' : 'low' },
            ];
            const aoa = [['曲线类型', '情景序号', '产品类别', '产品序号', '情景档'].concat(years.map(String))];
            for (const blk of blocks) {
                DISCOUNT_PRODUCTS.forEach((p, i) => {
                    const arr = blk.get(p);
                    aoa.push([i === 0 ? blk.name : '', blk.idx, blk.cat ? p.cat : '', p.seq, blk.lvl(p)].concat(years.map(t => arr[t] == null ? null : +(arr[t] * 100).toFixed(4))));
                });
            }
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), '输出');
            appendDiscountParamsSheet(wb, d);
            XLSX.writeFile(wb, discountXLSXName('输出'));
        }
        function downloadDiscountWorkingXLSX() {
            const d = ensureDiscountData(); if (!d) return;
            const years = d.years;
            const wb = XLSX.utils.book_new();
            // 负债曲线过程
            const la = [['年', '市场750(%)', '基础G(%)', '外推F(%)', '溢价高H(%)', '溢价中I(%)', '溢价低J(%)', '即期高K(%)', '即期中L(%)', '即期低M(%)', '远期中N(%)', '远期中O(%)', '远期低P(%)']];
            years.forEach(t => la.push([
                t, pct(d.liab._mkt[t]), pct(d.liab._base[t]), pct(d.liab._extr[t]),
                pct(d.liab._spreadHigh[t]), pct(d.liab._spreadMid[t]), pct(d.liab._spreadLow[t]),
                pct(d.liab.spot.high[t]), pct(d.liab.spot.mid[t]), pct(d.liab.spot.low[t]),
                pct(d.liab.fwd.high[t]), pct(d.liab.fwd.mid[t]), pct(d.liab.fwd.low[t])
            ]));
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(la), '负债曲线过程');
            // 利率曲线过程
            const ra = [['年', '市场60E60(%)', '上升Fup(%)', '下降Gdn(%)', '基础外推H(%)', '上升外推I(%)', '下降外推J(%)', '混合基础K(%)', '混合上升L(%)', '混合下降M(%)', '溢价N(%)', '即期基础O(%)', '即期上升P(%)', '即期下降Q(%)', '远期基础R(%)', '远期上升S(%)', '远期下降T(%)']];
            years.forEach(t => ra.push([
                t, pct(d.rate._mkt[t]), pct(d.rate._fup[t]), pct(d.rate._gdn[t]),
                pct(d.rate._extr.base[t]), pct(d.rate._extr.up[t]), pct(d.rate._extr.down[t]),
                pct(d.rate._base.base[t]), pct(d.rate._base.up[t]), pct(d.rate._base.down[t]),
                pct(d.rate._spread[t]),
                pct(d.rate.spot.base[t]), pct(d.rate.spot.up[t]), pct(d.rate.spot.down[t]),
                pct(d.rate.fwd.base[t]), pct(d.rate.fwd.up[t]), pct(d.rate.fwd.down[t])
            ]));
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(ra), '利率曲线过程');
            // 汇总
            const groups = [
                ['负债即期-高', d.liab.spot.high], ['负债即期-中', d.liab.spot.mid], ['负债即期-低', d.liab.spot.low],
                ['负债远期-高', d.liab.fwd.high], ['负债远期-中', d.liab.fwd.mid], ['负债远期-低', d.liab.fwd.low],
                ['利率即期-基础', d.rate.spot.base], ['利率即期-上升', d.rate.spot.up], ['利率即期-下降', d.rate.spot.down],
                ['利率远期-基础', d.rate.fwd.base], ['利率远期-上升', d.rate.fwd.up], ['利率远期-下降', d.rate.fwd.down]
            ];
            const sa = [['年'].concat(groups.map(g => g[0]))];
            years.forEach(t => { const row = [t]; groups.forEach(g => row.push(g[1][t] == null ? null : +(g[1][t] * 100).toFixed(4))); sa.push(row); });
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(sa), '汇总');
            XLSX.writeFile(wb, discountXLSXName('底稿'));
        }