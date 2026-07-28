// ================================================================
// 保险公司资本补充债 / 永续债 发行信息板块（ins_bonds）子系统
// 该文件由 index.html 拆分而来，随 index.html 以 <script defer> 加载（DOM 解析后、DOMContentLoaded 前执行）。
// 与主页共享全局作用域（classic script 顶层声明即全局）：
//   依赖全局：echarts（CDN）、insBonds（主页 loadInsBonds 异步写入）、showMainView / showDetail / currentMainView。
//   导出全局（供主页调用）：renderBondsView、populateIndustryYear、populateEndYearSelect、populateSummaryYearSelect、handleHashChange，
//             以及模块状态 bondScatterChart / bondComboChart / bondIndustryChart / industryYear / comboEndYear / scatterEndYear / summaryYear。
// 注意：本板块无访问码门控，故采用静态 defer 加载（不同于 discount-module.js 的懒加载）。
// ================================================================

        let bondScatterChart = null;
        let bondComboChart = null;
        let bondIndustryChart = null;
        let industryYear = '';
        let comboEndYear = '';
        let scatterEndYear = '';
        let summaryYear = 'all';
        const zhCollator = new Intl.Collator('zh-Hans-CN');

        // 全量债券（顶部汇总卡 / 公司×年表 使用，不受截止年份影响）
        function allBonds() {
            return (insBonds && insBonds.bonds) ? insBonds.bonds : [];
        }
        // 行业汇总面板专有的年份筛选
        function industryRows() {
            const rows = allBonds();
            if (!industryYear) return rows;
            return rows.filter(b => (b.issueDate || '').slice(0, 4) === industryYear);
        }
        // 近五年滑动窗口（endYear 向前 4 年）
        function winRows(endY) {
            const rows = allBonds();
            if (!endY) return rows;
            const lo = String(parseInt(endY, 10) - 4);
            return rows.filter(b => {
                const y = (b.issueDate || '').slice(0, 4);
                return y >= lo && y <= endY;
            });
        }

        function wavgRate(rows) {
            let w = 0, s = 0;
            rows.forEach(b => { const r = (b.couponRate != null ? b.couponRate : null); const a = (b.issueAmnt != null ? b.issueAmnt : null); if (r != null && a != null) { w += r * a; s += a; } });
            return s > 0 ? w / s : null;
        }
        function maxRate(rows) { const r = rows.map(b => b.couponRate).filter(v => v != null); return r.length ? Math.max(...r) : null; }
        function minRate(rows) { const r = rows.map(b => b.couponRate).filter(v => v != null); return r.length ? Math.min(...r) : null; }
        function fmtRate(r) { return r == null ? '—' : (r.toFixed(2) + '%'); }
        function fmtAmnt(a) { return a == null ? '—' : (Math.round(a * 100) / 100).toFixed(1); }

        function renderBondsView() {
            const wrap = document.getElementById('viewBonds');
            if (!insBonds || !insBonds.bonds) {
                wrap.querySelector('.summary-grid').innerHTML = '<div class="loading-overlay"><div class="spinner"></div><p>资本债数据加载中…</p></div>';
                return;
            }
            const rows = allBonds();
            const comboWin = winRows(comboEndYear);
            const scatterWin = winRows(scatterEndYear);
            const rangeNote = insBonds.note || '';
            const minYear = rows.reduce((m, b) => {
                const y = parseInt((b.issueDate || '').slice(0, 4), 10);
                return (y && (!m || y < m)) ? y : m;
            }, null);
            document.getElementById('bondRangeNote').textContent =
                (rangeNote ? rangeNote + '　' : '') + `数据截至 ${insBonds.generatedAt || '—'}` + (minYear ? `（统计自${minYear}年起）` : '');
            // 顶部汇总卡：受「汇总年份」筛选控制（默认全部）；公司×年表：全量
            const summaryRows = summaryYear === 'all' ? rows : rows.filter(b => (b.issueDate || '').slice(0, 4) === summaryYear);
            renderBondSummaryCards(summaryRows);
            renderBondCompanyYear(rows);
            // 组合图 + 年度表：受组合图截止年份控制
            renderBondComboChart(comboWin);
            renderBondYearTable(rows);
            // 行业汇总：独立的行业年份下拉
            renderBondIndustryTable(industryRows());
            renderBondIndustryChart(industryRows());
            // 散点图：受散点截止年份控制
            renderBondScatter(scatterWin);
        }

        function renderBondSummaryCards(rows) {
            const total = rows.reduce((s, b) => s + (b.issueAmnt || 0), 0);
            const cap = rows.filter(b => b.bondType === '资本补充债').reduce((s, b) => s + (b.issueAmnt || 0), 0);
            const perp = rows.filter(b => b.bondType === '永续债').reduce((s, b) => s + (b.issueAmnt || 0), 0);
            const live = rows.filter(b => b.status === '存续');
            const liveTotal = live.reduce((s, b) => s + (b.issueAmnt || 0), 0);
            const liveCap = live.filter(b => b.bondType === '资本补充债').reduce((s, b) => s + (b.issueAmnt || 0), 0);
            const livePerp = live.filter(b => b.bondType === '永续债').reduce((s, b) => s + (b.issueAmnt || 0), 0);
            const wa = wavgRate(rows);
            const hi = maxRate(rows);
            const lo = minRate(rows);
            const fmtPair = (v, unit) => v + ' <small style="font-size:11px;font-weight:500;color:#94a3b8">' + unit + '</small>';
            const html = `
                <div class="summary-col col-issue">
                    <div class="summary-col-head">
                        <span class="ico">📊</span>
                        <span class="title">发行总额</span>
                        <span class="sub">亿元 · ${rows.length} 只</span>
                    </div>
                    <div class="summary-rows">
                        <div class="summary-row is-main"><span class="lk">合计</span><span class="lv">${fmtAmnt(total)}</span></div>
                        <div class="summary-row is-sub"><span class="lk">其中 · 资本补充债</span><span class="lv">${fmtAmnt(cap)}</span></div>
                        <div class="summary-row is-sub"><span class="lk">其中 · 永续债</span><span class="lv">${fmtAmnt(perp)}</span></div>
                    </div>
                </div>
                <div class="summary-col col-live">
                    <div class="summary-col-head">
                        <span class="ico">💎</span>
                        <span class="title">存续规模</span>
                        <span class="sub">亿元 · ${live.length} 只</span>
                    </div>
                    <div class="summary-rows">
                        <div class="summary-row is-main"><span class="lk">合计</span><span class="lv">${fmtAmnt(liveTotal)}</span></div>
                        <div class="summary-row is-sub"><span class="lk">其中 · 资本补充债</span><span class="lv">${fmtAmnt(liveCap)}</span></div>
                        <div class="summary-row is-sub"><span class="lk">其中 · 永续债</span><span class="lv">${fmtAmnt(livePerp)}</span></div>
                    </div>
                </div>
                <div class="summary-col col-rate">
                    <div class="summary-col-head">
                        <span class="ico">📈</span>
                        <span class="title">票面利率</span>
                        <span class="sub">% · ${rows.length} 只</span>
                    </div>
                    <div class="summary-rows">
                        <div class="summary-row is-main"><span class="lk">加权平均</span><span class="lv">${fmtRate(wa)}</span></div>
                        <div class="summary-row is-sub"><span class="lk">最高</span><span class="lv">${fmtRate(hi)}</span></div>
                        <div class="summary-row is-sub"><span class="lk">最低</span><span class="lv">${fmtRate(lo)}</span></div>
                    </div>
                </div>
            `;
            document.getElementById('bondSummaryCards').innerHTML = html;
        }

        function renderBondYearTable(rows) {
            const map = {};
            rows.forEach(b => {
                const y = (b.issueDate || '').slice(0, 4);
                if (!y) return;
                if (!map[y]) map[y] = [];
                map[y].push(b);
            });
            const years = Object.keys(map).sort((a, b) => b - a);
            // 三列利率的极值，用于高/低配色
            const waArr = [], hiArr = [], loArr = [];
            years.forEach(y => {
                const g = map[y];
                waArr.push(wavgRate(g)); hiArr.push(maxRate(g)); loArr.push(minRate(g));
            });
            const rateCell = (arr, v) => {
                if (v == null) return '';
                const mx = Math.max(...arr), mn = Math.min(...arr);
                if (mx === mn) return '';
                if (v === mx) return ' class="cell-hi"';
                if (v === mn) return ' class="cell-lo"';
                return '';
            };
            // 加权利率 / 最高 / 最低 三列按全局数值连续红→绿渐变着色（高=红，低=绿，中间过渡）
            const allRates = [].concat(waArr, hiArr, loArr).filter(v => v != null);
            const rMin = allRates.length ? Math.min(...allRates) : 0;
            const rMax = allRates.length ? Math.max(...allRates) : 1;
            const rateStyle = v => {
                if (v == null) return '';
                const t = rMax === rMin ? 0.5 : (v - rMin) / (rMax - rMin);
                const hue = Math.round(120 * (1 - t)); // 高值红(0) → 低值绿(120)
                return ` style="background:hsl(${hue},72%,92%);color:hsl(${hue},68%,30%);font-weight:700"`;
            };
            let html = '<colgroup><col style="width:7%"><col style="width:18%"><col style="width:15%"><col style="width:15%"><col style="width:15%"><col style="width:10%"><col style="width:10%"><col style="width:10%"></colgroup>'
                + '<thead><tr><th class="lft">发行年份</th><th>只数</th><th>总额(亿)</th><th>资本补充债(亿)</th><th>永续债(亿)</th><th>加权利率</th><th>最高</th><th>最低</th></tr></thead><tbody>';
            years.forEach((y, i) => {
                const g = map[y];
                const total = g.reduce((s, b) => s + (b.issueAmnt || 0), 0);
                const cap = g.filter(b => b.bondType === '资本补充债').reduce((s, b) => s + (b.issueAmnt || 0), 0);
                const perp = g.filter(b => b.bondType === '永续债').reduce((s, b) => s + (b.issueAmnt || 0), 0);
                const capN = g.filter(b => b.bondType === '资本补充债').length;
                const perpN = g.filter(b => b.bondType === '永续债').length;
                html += `<tr><td class="lft">${y}</td><td><b>${g.length}</b><span class="cnt-sub">（资补${capN}；永续${perpN}）</span></td><td><b>${fmtAmnt(total)}</b></td><td>${fmtAmnt(cap)}</td><td>${fmtAmnt(perp)}</td>`
                    + `<td${rateStyle(waArr[i])}>${fmtRate(waArr[i])}</td>`
                    + `<td${rateStyle(hiArr[i])}>${fmtRate(hiArr[i])}</td>`
                    + `<td${rateStyle(loArr[i])}>${fmtRate(loArr[i])}</td></tr>`;
            });
            html += '</tbody>';
            document.getElementById('bondYearTable').innerHTML = html;
        }

        function renderBondIndustryTable(rows) {
            const map = {};
            rows.forEach(b => {
                const k = b.industry || '其他';
                if (!map[k]) map[k] = [];
                map[k].push(b);
            });
            const order = ['寿险', '产险', '再保', '集团', '其他'];
            const keys = Object.keys(map).sort((a, b) => order.indexOf(a) - order.indexOf(b));
            let html = '<thead><tr><th class="lft">行业</th><th>只数</th><th>总额(亿)</th><th>加权利率</th><th>最高</th><th>最低</th></tr></thead><tbody>';
            keys.forEach(k => {
                const g = map[k];
                const total = g.reduce((s, b) => s + (b.issueAmnt || 0), 0);
                html += `<tr><td class="lft">${k}</td><td>${g.length}</td><td>${fmtAmnt(total)}</td><td>${fmtRate(wavgRate(g))}</td><td>${fmtRate(maxRate(g))}</td><td>${fmtRate(minRate(g))}</td></tr>`;
            });
            html += '</tbody>';
            document.getElementById('bondIndustryTable').innerHTML = html;
        }

        function renderBondIndustryChart(rows) {
            const dom = document.getElementById('bondIndustryChart');
            if (typeof echarts === 'undefined') { dom.innerHTML = '<p style="color:#999;padding:10px;">图表库未加载</p>'; return; }
            if (!bondIndustryChart) bondIndustryChart = echarts.init(dom);
            const map = {};
            rows.forEach(b => { const k = b.industry || '其他'; map[k] = (map[k] || 0) + (b.issueAmnt || 0); });
            const order = ['寿险', '产险', '再保', '集团', '其他'];
            const keys = Object.keys(map).sort((a, b) => order.indexOf(a) - order.indexOf(b));
            const colors = { '寿险': '#3b7dd8', '产险': '#e6a23c', '再保': '#67c23a', '集团': '#9b59b6', '其他': '#95a5a6' };
            const total = keys.reduce((s, k) => s + map[k], 0);
            const data = keys.map(k => ({
                name: k,
                value: +(map[k].toFixed(1)),
                pct: total > 0 ? (map[k] / total * 100).toFixed(1) : 0,
                itemStyle: { color: colors[k] || '#3b7dd8' },
            }));
            bondIndustryChart.setOption({
                tooltip: { trigger: 'item', formatter: p => `${p.name}<br/>发行额：${p.value} 亿<br/>占比：${p.percent.toFixed(1)}%` },
                legend: { orient: 'horizontal', top: 2, left: 'center', itemWidth: 10, itemHeight: 10, itemGap: 10, textStyle: { fontSize: 11, color: '#3e5a7a' } },
                series: [{
                    type: 'pie', radius: ['42%', '66%'], center: ['50%', '56%'],
                    avoidLabelOverlap: true, data,
                    label: { show: true, position: 'outside', formatter: p => `${p.percent.toFixed(1)}%`, fontSize: 11, color: '#3e5a7a' },
                    labelLine: { length: 6, length2: 10 },
                }],
            }, true);
        }

        function renderBondCompanyYear(rows) {
            const comps = {}, years = new Set(), allVals = [];
            rows.forEach(b => {
                const c = b.issuer || '未知';
                const y = (b.issueDate || '').slice(0, 4);
                if (!y) return;
                years.add(y);
                if (!comps[c]) comps[c] = {};
                const v = (b.issueAmnt || 0);
                comps[c][y] = (comps[c][y] || 0) + v;
                allVals.push(v);
            });
            // 年份倒序：合计列在第一位
            const yArr = [...years].sort((a, b) => b - a);
            // 全局发行额范围（用于圆点大小 / 颜色深浅）
            const vMin = allVals.length ? Math.min(...allVals) : 0;
            const vMax = allVals.length ? Math.max(...allVals) : 1;
            // 单格圆点样式：尺寸 5~13px，颜色由浅灰渐变到红色（HSL 连续过渡）
            const dotStyle = v => {
                if (vMax === vMin) return 'width:9px;height:9px;background:hsl(0,75%,42%)';
                const t = Math.max(0, Math.min(1, (v - vMin) / (vMax - vMin)));
                const size = 5 + 8 * t;
                const sat = Math.round(75 * t);
                const light = Math.round(82 - 40 * t);
                return `width:${size}px;height:${size}px;background:hsl(0,${sat}%,${light}%)`;
            };
            // colgroup：序号 5% / 发行人 22% / 合计 8% / 9 个年份 auto 均分剩余 65%
            const yrCols = yArr.map(() => '<col>').join('');
            let html = `<colgroup><col class="col-idx"><col class="col-issuer"><col class="col-total">${yrCols}</colgroup>`
                + '<thead><tr><th class="lft col-idx">#</th><th class="lft col-issuer">发行人</th><th>合计</th>'
                + yArr.map(y => `<th>${y}</th>`).join('') + '</tr></thead><tbody>';
            // 按合计降序，同额按拼音
            const sorted = Object.keys(comps).sort((a, b) => {
                const ta = Object.values(comps[a]).reduce((s, v) => s + v, 0);
                const tb = Object.values(comps[b]).reduce((s, v) => s + v, 0);
                if (tb !== ta) return tb - ta;
                return zhCollator.compare(a, b);
            });
            sorted.forEach((c, idx) => {
                let tot = 0;
                const tds = yArr.map(y => {
                    const v = comps[c][y];
                    if (v) { tot += v; return `<td class="t-num"><span class="dot" style="${dotStyle(v)}"></span>${fmtAmnt(v)}</td>`; }
                    return '<td>—</td>';
                }).join('');
                html += `<tr><td class="lft col-idx">${idx + 1}</td><td class="lft col-issuer" title="${c}">${c}</td><td class="t-num"><b>${fmtAmnt(tot)}</b></td>${tds}</tr>`;
            });
            html += '</tbody>';
            document.getElementById('bondCompanyYearTable').innerHTML = html;
        }

        function renderBondScatter(rows) {
            const dom = document.getElementById('bondScatterChart');
            if (typeof echarts === 'undefined') { dom.innerHTML = '<p style="color:#999;padding:20px;">图表库未加载</p>'; return; }
            if (!bondScatterChart) bondScatterChart = echarts.init(dom);
            const years = [...new Set(rows.map(b => (b.issueDate || '').slice(0, 4)).filter(Boolean))].sort();
            const colorOf = { '存续': '#2e9e5b', '已赎回': '#d9534f', '已到期': '#9aa7b8' };
            const byStatus = {};
            rows.forEach(b => {
                const y = (b.issueDate || '').slice(0, 4);
                if (!y || b.couponRate == null) return;
                (byStatus[b.status] = byStatus[b.status] || []).push([y, b.couponRate, b.issuer, b.bondShort, b.issueAmnt, b.industry]);
            });
            const series = Object.keys(byStatus).map(st => ({
                name: st,
                type: 'scatter',
                symbolSize: d => Math.max(3, Math.min(10, (d[4] || 5) * 0.35)),
                itemStyle: { color: colorOf[st] || '#888', opacity: 0.78 },
                data: byStatus[st],
            }));
            bondScatterChart.setOption({
                tooltip: { trigger: 'item', formatter: p => `${p.data[3]}<br/>${p.data[2]}（${p.data[5]}）<br/>发行日：${p.data[0]} · 票面利率：${p.data[1]}%<br/>发行额：${fmtAmnt(p.data[4])} 亿` },
                legend: { data: Object.keys(byStatus), top: 0 },
                grid: { left: 50, right: 24, top: 36, bottom: 40 },
                xAxis: { type: 'category', data: years, name: '发行年份', axisLabel: { rotate: 45 } },
                yAxis: { type: 'value', name: '票面利率(%)', scale: true },
                series,
            }, true);
        }

        function renderBondComboChart(rows) {
            const dom = document.getElementById('bondComboChart');
            if (typeof echarts === 'undefined') { dom.innerHTML = '<p style="color:#999;padding:20px;">图表库未加载</p>'; return; }
            if (!bondComboChart) bondComboChart = echarts.init(dom);
            const map = {};
            rows.forEach(b => {
                const y = (b.issueDate || '').slice(0, 4);
                if (!y) return;
                const m = map[y] || (map[y] = { cap: 0, perp: 0, wsum: 0, wn: 0, hi: -Infinity, lo: Infinity });
                if (b.bondType === '永续债') m.perp += b.issueAmnt || 0;
                else m.cap += b.issueAmnt || 0;
                if (b.couponRate != null) {
                    m.wsum += b.couponRate * (b.issueAmnt || 0);
                    m.wn += (b.issueAmnt || 0);
                    if (b.couponRate > m.hi) m.hi = b.couponRate;
                    if (b.couponRate < m.lo) m.lo = b.couponRate;
                }
            });
            const years = Object.keys(map).sort((a, b) => a - b);
            const capArr = years.map(y => +(map[y].cap.toFixed(1)));
            const perpArr = years.map(y => +(map[y].perp.toFixed(1)));
            const totalArr = years.map(y => +((map[y].cap + map[y].perp).toFixed(1)));
            const waArr = years.map(y => map[y].wn > 0 ? +(map[y].wsum / map[y].wn).toFixed(2) : null);
            const hiArr = years.map(y => isFinite(map[y].hi) ? +(map[y].hi.toFixed(2)) : null);
            const loArr = years.map(y => isFinite(map[y].lo) ? +(map[y].lo.toFixed(2)) : null);
            bondComboChart.setOption({
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
                    formatter: p => {
                        let s = p[0].axisValue + ' 年<br/>';
                        p.forEach(it => { if (it.seriesName !== '柱顶总额') s += `${it.marker}${it.seriesName}：${it.value}${it.seriesName.includes('利率') ? '%' : ' 亿'}<br/>`; });
                        return s;
                    } },
                legend: { data: ['资本补充债', '永续债', '加权平均利率', '最高利率', '最低利率'], top: 4 },
                grid: { left: 56, right: 56, top: 56, bottom: 36 },
                xAxis: { type: 'category', data: years, axisLabel: { fontSize: 12 }, axisTick: { alignWithLabel: true } },
                yAxis: [
                    { type: 'value', name: '发行额(亿)', position: 'left', nameTextStyle: { fontSize: 11 } },
                    { type: 'value', name: '利率(%)', position: 'right', min: v => Math.max(0, (v.min - 0.5).toFixed(2)), max: v => (v.max + 0.5).toFixed(2), splitLine: { show: false }, nameTextStyle: { fontSize: 11 } },
                ],
                series: [
                    { name: '资本补充债', type: 'bar', stack: 'amt', data: capArr, itemStyle: { color: '#3b7dd8' }, barMaxWidth: 32 },
                    { name: '永续债', type: 'bar', stack: 'amt', data: perpArr, itemStyle: { color: '#e6a23c' }, barMaxWidth: 32 },
                    // 总额作为数据标签：放在柱顶（不参与堆叠，避免轴被翻倍）
                    { name: '柱顶总额', type: 'bar', data: totalArr, itemStyle: { color: 'transparent' }, barMaxWidth: 32,
                      label: { show: true, position: 'top', distance: 4, formatter: p => p.value, color: '#1f4a7a', fontSize: 10, fontWeight: 600 } },
                    { name: '加权平均利率', type: 'line', yAxisIndex: 1, data: waArr, symbol: 'circle', symbolSize: 8,
                      lineStyle: { width: 2.5, color: '#8e44ad' }, itemStyle: { color: '#8e44ad' } },
                    { name: '最高利率', type: 'line', yAxisIndex: 1, data: hiArr, symbol: 'triangle', symbolSize: 8,
                      lineStyle: { width: 1.5, color: '#e74c3c', type: 'dashed' }, itemStyle: { color: '#e74c3c' } },
                    { name: '最低利率', type: 'line', yAxisIndex: 1, data: loArr, symbol: 'diamond', symbolSize: 8,
                      lineStyle: { width: 1.5, color: '#2e9e5b', type: 'dashed' }, itemStyle: { color: '#2e9e5b' } },
                ],
            }, true);
        }

        function downloadBondDetails() {
            if (!insBonds || !insBonds.bonds) return;
            const cols = [
                ['issuer', '发行人'], ['bondShort', '债券简称'], ['industry', '行业'],
                ['bondType', '类型'], ['issueDate', '发行日'], ['bondPeriod', '期限'],
                ['issueAmnt', '发行额(亿)'], ['couponRate', '票面利率%'], ['debtRating', '债项评级'],
                ['status', '状态'], ['mrtyDate', '到期日'], ['valueDate', '起息日'], ['ratingStr', '主体/债项'],
            ];
            const esc = v => { let s = (v == null ? '' : String(v)); if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"'; return s; };
            let csv = '﻿' + cols.map(c => esc(c[1])).join(',') + '\n';
            insBonds.bonds.forEach(b => { csv += cols.map(c => esc(b[c[0]])).join(',') + '\n'; });
            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = '保险公司资本补充债明细_' + (insBonds.generatedAt || '') + '.csv';
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        function populateIndustryYear() {
            if (!insBonds || !insBonds.bonds) return;
            const ys = [...new Set(insBonds.bonds.map(b => (b.issueDate || '').slice(0, 4)).filter(Boolean))].sort((a, b) => b - a);
            const sel = document.getElementById('bondIndustryYear');
            sel.innerHTML = '<option value="">全部年份</option>' + ys.map(y => `<option value="${y}">${y} 年</option>`).join('');
        }

        // 截止年份下拉（组合图 / 散点图 各一个），默认取最新发行年份
        function populateEndYearSelect(selId, setter) {
            if (!insBonds || !insBonds.bonds) return;
            const ys = [...new Set(insBonds.bonds.map(b => (b.issueDate || '').slice(0, 4)).filter(Boolean))].sort((a, b) => b - a);
            const sel = document.getElementById(selId);
            if (!sel) return;
            sel.innerHTML = ys.map(y => `<option value="${y}">截至 ${y} 年</option>`).join('');
            const def = ys[0] || '';
            sel.value = def;
            setter(def);
        }

        function populateSummaryYearSelect() {
            if (!insBonds || !insBonds.bonds) return;
            const ys = [...new Set(insBonds.bonds.map(b => (b.issueDate || '').slice(0, 4)).filter(Boolean))].sort((a, b) => b - a);
            const sel = document.getElementById('bondSummaryYear');
            if (!sel) return;
            // 保留第一个「全部」选项
            const opts = ['<option value="all">全部</option>'].concat(ys.map(y => `<option value="${y}">${y} 年</option>`)).join('');
            sel.innerHTML = opts;
            sel.value = summaryYear;
        }

        function handleHashChange() {
            const hash = location.hash.replace('#', '');
            const validTypes = ['gov_spot', 'gov_ytm', 'cdb_spot', 'cdb_ytm'];
            if (validTypes.includes(hash)) {
                currentMainView = 'monitor';
                showMainView('monitor');
                showDetail(hash);
            } else {
                showMainView(currentMainView || 'monitor');
            }
        }

        // ================================================================

// ---- 以下监听绑定在 bonds.js 加载时（DOM 已就绪）执行一次 ----
        // 保险公司资本补充债发行信息 板块：下载 + 行业年份 + 组合图/散点截止年份
        const dlBtn = document.getElementById('bondDownloadBtn');
        if (dlBtn) dlBtn.addEventListener('click', downloadBondDetails);
        const iySel = document.getElementById('bondIndustryYear');
        if (iySel) iySel.addEventListener('change', function() {
            industryYear = this.value;
            renderBondIndustryTable(industryRows());
            renderBondIndustryChart(industryRows());
        });
        // 组合图截止年份
        const coSel = document.getElementById('bondComboEndYear');
        if (coSel) coSel.addEventListener('change', function() {
            comboEndYear = this.value;
            const w = winRows(comboEndYear);
            renderBondComboChart(w);
            renderBondYearTable(allBonds());
        });
        // 散点图截止年份
        const scSel = document.getElementById('bondScatterEndYear');
        if (scSel) scSel.addEventListener('change', function() {
            scatterEndYear = this.value;
            renderBondScatter(winRows(scatterEndYear));
        });
        // 顶部汇总卡：汇总年份筛选
        const sySel = document.getElementById('bondSummaryYear');
        if (sySel) sySel.addEventListener('change', function() {
            summaryYear = this.value;
            const rows = allBonds();
            const summaryRows = summaryYear === 'all' ? rows : rows.filter(b => (b.issueDate || '').slice(0, 4) === summaryYear);
            renderBondSummaryCards(summaryRows);
        });

