## 这个 PR 做了什么

<!-- 一两句话 -->

## 关联 Issue

Closes #

## 自检

- [ ] `cd python && python -m unittest discover -s tests` 全绿
- [ ] `node --check lib/index.js` 通过
- [ ] 如果改了撮合/账本：确认「期末权益 − 初始资金 == Σ交易净盈亏」仍成立
- [ ] 如果改了事件顺序：确认「信号 t 收盘产生、t+1 开盘成交」没被破坏
- [ ] 文档/CHANGELOG 已同步
