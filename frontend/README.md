# 多用户网页工作台

Next.js 应用与服务端 API 代理在业务服务器运行。客户机只需浏览器。安装与构建：

```bash
pnpm install --frozen-lockfile --ignore-scripts
pnpm typecheck
pnpm test
pnpm build
```

服务端环境变量见 `.env.example`，与 [服务器部署指南](../docs/DEPLOYMENT.md) 配套使用。凭据不得使用 `NEXT_PUBLIC_*`。角色授权由后端解析，不接受浏览器自报的身份或角色。

前端参考来源与版权见 [SOURCE.md](../SOURCE.md) 及 [LICENSE](LICENSE)。
