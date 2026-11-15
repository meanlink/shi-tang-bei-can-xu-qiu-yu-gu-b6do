# 厨房备餐与跨档口调拨 — 验收测试

本目录存放模块的独立验收测试，源码树不含测试与运行产物。

## 后端测试（pytest，SQLite 文件库）

验证：备餐台账分次登记、领用耗用、调拨状态机、并发不超扣、幂等重试、
中断恢复、接收/退回互斥、全局数量守恒与 HTTP 鉴权。

```bash
# 在仓库根目录，使用 backend 的虚拟环境
backend/.venv/bin/python -m pytest test-artifacts/kitchen-transfer/tests -q
```

并发超扣用例依赖条件原子 UPDATE（`WHERE 可用量 >= 申请量`），
该守卫在 MySQL/InnoDB 与 SQLite 上语义一致，生产 MySQL 下另有行锁串行化。

## 前端测试（jsdom，原生 ES Module，无打包步骤）

验证：两个标签页的模板渲染、接口数据绑定、幂等键提交、
调拨事件流水追查以及角色（viewer/admin）操作入口差异。

```bash
cd test-artifacts/kitchen-transfer/ui
npm install
npm test
```

测试通过 fetch 桩模拟后端，不依赖运行中的服务；浏览器全局由 jsdom 提供。
