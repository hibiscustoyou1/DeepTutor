# Open DeepTutor 本地开发与打包指南

完整编译打包、注入 ctcloud 凭证、管理签名密钥，以及常见故障排查。

## 1. 组件一览

| 文件 | 作用 |
| --- | --- |
| `scripts/build-with-secrets.mjs` | 一键构建：runtime → 注入凭证 → 重密封 → 冒烟 → 打包 |
| `scripts/inject-ctcloud-secrets.mjs` | 把 `CTCLOUD_APP_CODE/SECRET` 写入捆绑 core 的 `partners/channels/_ctcloud_secrets.py` |
| `scripts/lib/ctcloud-credentials.mjs` | 凭证加载（环境变量优先，其次仓库根目录 `.env`） |
| `.github/workflows/ctcloud-desktop-build.yml` | CI 版同流程：发布模式挂到 Release，验证模式存为 artifact |
| `.github/workflows/desktop-release.yml` | 通用发布工作流（只含仓库内占位凭证） |

## 2. 环境要求

| 依赖 | 版本 / 位置 | 说明 |
| --- | --- | --- |
| Node.js | 22+ | 跑构建脚本与 Electron 打包 |
| uv | 任意较新版本 | 自动下载并安装捆绑的 Python 3.12 |
| NSIS (`makensis`) | `C:\Program Files (x86)\NSIS\` | Windows 安装器必需；`platforms/windows-x64/package.mjs` 自动探测 |
| Git | — | `build:runtime` 用 `git archive HEAD` 导出源码 |

macOS 目标必须在 macOS 上构建（`macos-x64` 可在 Apple Silicon 上通过
Rosetta 构建）。

## 3. 首次准备

```powershell
# 1) 安装桌面 shell 依赖
cd apps\desktop
npm ci

# 2) 准备 ctcloud 凭证（仓库根目录 .env，已 gitignore）
#    CTCLOUD_APP_CODE=<YL-3rd-Appcode>
#    CTCLOUD_APP_SECRET=<sk>

# 3) 准备签名私钥（见第 7 节）
#    放到 apps/desktop/.local/signing-private.pem
#    或设置环境变量 OPEN_DEEPTUTOR_SIGNING_KEY
```

## 4. 日常开发

```powershell
cd apps\desktop

# 静态检查 + 单元测试（提交前必跑）
npm run check
npm test

# 用本机捆绑的 core 启动 shell（需先成功执行过一次 build:runtime）
npm start
```

- 外壳代码在 `src/`，各平台打包配置在 `platforms/<target>/`。
- 改 `src/` 后 `npm start` 直接生效；改 core（Python/前端）源码需要重新
  `build:runtime`。
- 构建始终基于已提交代码；开发中想验证未提交的 core 改动，用
  `DEEPTUTOR_SOURCE` 指向一个干净 checkout。
- 重新生成图标（macOS）：`npm run icon`。

## 5. 一键本地构建（推荐）

```powershell
# 仓库根目录执行
node .\apps\desktop\scripts\build-with-secrets.mjs --target windows-x64
```

内部依次执行 5 步（`build-with-secrets.mjs:44-48`）：

1. `build-runtime.mjs` — 下载目标 Node、`git archive HEAD` 导出源码、安装
   Python 依赖、构建 Next 前端、密封并签名 runtime
2. `inject-ctcloud-secrets.mjs` — 将凭证写入捆绑 core
3. `seal-runtime.mjs` — 重新打包并重新签名（manifest 必须覆盖注入文件）
4. `smoke.mjs` — 用捆绑的 Python/Node 真实启动核心，验证 UI / API / 代理 /
   干净退出（开发者工具链从 PATH 中移除）
5. `package.mjs` — 产出安装包

**产物**（`apps/desktop/out/`）：

- `Open-DeepTutor-Windows-v<版本>-Setup.exe` — NSIS 安装器
- `Open-DeepTutor-Windows-v<版本>-Portable.zip` — 便携版
- 未压缩的 Electron 应用目录

## 6. 分步构建（不含 ctcloud 凭证）

```powershell
cd apps\desktop
npm run check && npm test
npm run build:runtime -- --target windows-x64
npm run smoke -- --target windows-x64
npm run package -- --target windows-x64
```

注意：

- **必须先提交**：`build:runtime` 用 `git archive HEAD`，未提交的改动不会进包。
  可用 `DEEPTUTOR_SOURCE` 指向另一个已提交的 checkout。
- `--target` 缺省为本机。
- 分步流程不会注入 ctcloud 凭证，产出的应用走仓库内的 `"ak"/"sk"` 占位值。

## 7. 签名密钥管理

一个 Ed25519 密钥对所有平台通用；manifest 绑定平台与架构。

- **私钥**（不入库）：`apps/desktop/.local/signing-private.pem`，或环境变量
  `OPEN_DEEPTUTOR_SIGNING_KEY`（优先于文件）。
- **公钥**（入库、随应用发布）：`apps/desktop/assets/runtime-public.pem`。
- 密封时会校验私钥与公钥配对（`scripts/lib/signing.mjs`），不配对直接失败。

### 校验配对

```powershell
node -e "const{createPrivateKey,createPublicKey}=require('node:crypto'),fs=require('node:fs');console.log(createPublicKey(fs.readFileSync('apps/desktop/.local/signing-private.pem','utf8')).export({type:'spki',format:'pem'}))"
```

输出应与 `assets/runtime-public.pem` 内容一致。

### 轮换密钥（谨慎）

1. 生成新密钥对（`seal-runtime.mjs --new-key` 会在私钥缺失时自动生成，
   并**覆写** `assets/runtime-public.pem`）；
2. 提交新的 `assets/runtime-public.pem`；
3. 把新私钥 PEM 写入 GitHub 仓库 secret `DESKTOP_RUNTIME_SIGNING_KEY`；
4. 已发布的旧应用无法再接受新签名，旧安装升级后需清理本地状态（见 8.2）。

> `--new-key` 产出的公钥变更**绝不能随意提交**——一旦提交，所有已发布应用
> （内嵌旧公钥）都会拒绝新签名的 core，CI 发布也会受影响。

## 8. 故障排查

### 8.1 `The signing key does not match assets/runtime-public.pem`

本地 `.local/signing-private.pem` 与仓库公钥不配对（常见于被
`OPEN_DEEPTUTOR_SIGNING_KEY` 覆写、或换过密钥）。用第 7 节命令确认后，
恢复配对私钥，或通过环境变量传入正确私钥：

```powershell
$env:OPEN_DEEPTUTOR_SIGNING_KEY = Get-Content .\path\to\correct-private.pem -Raw
node .\apps\desktop\scripts\build-with-secrets.mjs --target windows-x64
```

### 8.2 安装后启动报 `The core bundle signature is invalid`

不是安装包的问题（冒烟测试已用同一公钥验签通过）。是**本机残留的旧安装
状态**：`active.json` 记录的上次运行时由旧密钥签名，新应用用新公钥验不过，
启动直接报错（`src/bundles.mjs:56` → `verifyManifest`）。

清除旧状态（`workspace/` 内的知识库、聊天等数据会保留）：

```powershell
Remove-Item "$env:LOCALAPPDATA\Open DeepTutor\active.json" -Force
Remove-Item "$env:LOCALAPPDATA\Open DeepTutor\runtimes" -Recurse -Force  # 可选：释放磁盘
```

重启应用即回退到安装包内捆绑的 seed。

> 这是换密钥的必然结果（`seal-runtime.mjs` 注释：新密钥会孤立所有已发布的
> 应用）。若要让所有老用户平滑升级，可考虑修改 `src/bundles.mjs` 的
> `startupRuntime()`：saved 验签失败时回退到 seed 而非抛错。

### 8.3 首次运行 SmartScreen 提示

Windows 产物未做 Authenticode 签名，点「仍要运行」即可。

### 8.4 构建日志与冒烟结果

- `.local/<target>/smoke.log` — 冒烟测试完整日志
- `.local/<target>/smoke-result.json` — 冒烟测试结构化结果
- 运行期日志：`%LOCALAPPDATA%\Open DeepTutor\logs\runtime.log`

## 9. CI 

CI 必需 secrets：`CTCLOUD_APP_CODE`、`CTCLOUD_APP_SECRET`；可选
`DESKTOP_RUNTIME_SIGNING_KEY`（缺失时回退 `--new-key` 随机密钥——后续升级
会再次触发 8.2 的问题，正式构建务必配置）。

## 10. 相关文件索引

| 路径 | 说明 |
| --- | --- |
| `scripts/build-with-secrets.mjs` | 一键构建入口 |
| `scripts/build-runtime.mjs` | runtime 组装（源码快照、Python/Node/前端） |
| `scripts/seal-runtime.mjs` | 打包 + 签名 + 生成 `runtime.json` |
| `scripts/smoke.mjs` | 捆绑 core 冒烟测试 |
| `scripts/package.mjs` → `platforms/*/package.mjs` | 各平台产物 |
| `scripts/lib/signing.mjs` | 私钥/公钥配对校验 |
| `src/main.mjs` / `src/bundles.mjs` | Electron 外壳启动与验签逻辑 |
| `assets/runtime-public.pem` | 随应用发布的验签公钥 |
