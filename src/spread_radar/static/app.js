const form = document.querySelector('#scan-form');
const status = document.querySelector('#status');
const summary = document.querySelector('#summary');
const results = document.querySelector('#results');
const body = document.querySelector('#result-body');
const warnings = document.querySelector('#warnings');
const button = document.querySelector('#scan-button');
const source = document.querySelector('#source');
const network = document.querySelector('#network');

const money = (value) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value);
const price = (value) => Number(value).toPrecision(7);
const percent = (bps) => `${(Number(bps) / 100).toFixed(2)}%`;

function element(name, text, className) {
  const node = document.createElement(name);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function metric(value, label) {
  const node = document.createElement('div');
  node.append(element('strong', String(value)), element('span', label));
  return node;
}

function tableCell(text, className) {
  return element('td', text, className);
}

source.addEventListener('change', () => {
  if (source.value === 'okx' && network.value.trim() === 'bsc') network.value = '56';
  if (source.value === 'geckoterminal' && network.value.trim() === '56') network.value = 'bsc';
});

function updateResults(payload) {
  summary.hidden = false;
  summary.replaceChildren(
    metric(payload.candidate_count, '链上候选'),
    metric(payload.quote_count, 'CEX 报价'),
    metric(payload.opportunities.length, '通过筛选'),
  );
  results.hidden = false;
  body.replaceChildren();
  for (const item of payload.opportunities) {
    const row = document.createElement('tr');
    const tax = `买税 ${percent(item.token.buy_tax_bps || 0)} · 卖税 ${percent(item.token.sell_tax_bps || 0)}`;
    const tokenCell = document.createElement('td');
    const tokenDetails = document.createElement('small');
    tokenDetails.append(
      document.createTextNode(item.token.address),
      document.createElement('br'),
      document.createTextNode(tax),
    );
    tokenCell.append(
      element('strong', item.token.symbol),
      tokenDetails,
    );
    row.append(
      tokenCell,
      tableCell(item.quote.exchange),
      tableCell(item.quote.market_type),
      tableCell(price(item.token.onchain_price_usd)),
      tableCell(price(item.quote.bid)),
      tableCell(`$${money(item.quote.bid_depth_usd || 0)}`),
      tableCell(percent(item.gross_spread_bps)),
      tableCell(percent(item.net_spread_bps), 'net'),
      tableCell(`$${money(item.token.volume_24h_usd)}`),
      tableCell(`$${money(item.token.liquidity_usd)}`),
    );
    body.append(row);
  }
  if (!payload.opportunities.length) {
    const row = document.createElement('tr');
    const cell = tableCell('没有结果通过当前净价差阈值。', 'empty');
    cell.colSpan = 10;
    row.append(cell);
    body.append(row);
  }
  warnings.hidden = !payload.warnings.length;
  warnings.replaceChildren();
  if (payload.warnings.length) {
    const list = document.createElement('ul');
    for (const warning of payload.warnings) list.append(element('li', warning));
    warnings.append(element('h2', '数据告警'), list);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const markets = [...document.querySelectorAll('input[name="market"]:checked')].map((item) => item.value);
  const exchanges = [...document.querySelectorAll('input[name="exchange"]:checked')].map((item) => item.value);
  if (!markets.length) {
    status.textContent = '请至少选择一种市场类型。';
    return;
  }
  if (!exchanges.length) {
    status.textContent = '请至少选择一家交易所。';
    return;
  }
  const data = new FormData(form);
  data.set('market_types', markets.join(','));
  data.set('exchanges', exchanges.join(','));
  data.delete('market');
  data.delete('exchange');
  button.disabled = true;
  status.textContent = '正在获取链上候选和四所报价…';
  try {
    const response = await fetch(`/api/scan?${new URLSearchParams(data)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || '扫描失败');
    updateResults(payload);
    status.textContent = payload.cached ? '已显示 60 秒缓存结果。' : '扫描完成。';
  } catch (error) {
    status.textContent = `扫描失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
});
