# 胖乖生活 1.139.0 去广告版

基于原始 `base.apk` 制作的去广告版本，包名为 `com.qiekj.user`，版本号为 `1.139.0`（versionCode：234）。

**验证状态：2026-09-11，用户确认基础功能可用；2026-09-12 补充发现定位停留在北京、已授权仍提示“获取定位权限失败”。定位修复测试包待真机复测。**

## 安装包

- [定位修复测试 APK](output/胖乖生活-noads-locationfix-1.139.0.apk)
- [首版去广告 APK](output/乖胖生活-noads-1.139.0.apk)
- 原始 APK 需自行准备。脚本只接受下文 SHA-256 对应的 1.139.0 原包，未验证其他版本。
- [广告定位报告](广告定位报告.md)

首版去广告 APK 大小：241,997,063 字节，约 230.79 MiB。

SHA-256：

```text
28e03e201dc434994171ae24ccbeecc46c22b40a9319701fcb70f651b898570f
```

定位修复测试包 SHA-256：

```text
d25ae1b9fb12a82149f9a1a711805fde8691275931ba6811c2ad51bc6409dc11
```

## 去广告范围

| 类型 | 处理内容 |
| --- | --- |
| 冷启动开屏 | 停止请求开屏广告，通过原有关闭流程进入主界面 |
| 热启动开屏 | 移除从后台返回前台时的广告触发部分 |
| 首页、社区、设备和订单广告 | 过滤明确的广告位，隐藏悬浮、轮播、图片及信息流广告 |
| 插屏和激励视频 | 禁用已定位的 GroMore、Taku、小米及自建广告组件的加载、展示入口 |
| 扫码前下载推广 | 使用原有“继续使用设备”分支，跳过推广拦截 |
| 收银台和设备运行中推广 | 停止加载第三方下载任务卡、相关图片推广 |
| 商城列表广告 | 隐藏广告类型条目，保留正常商品类型的绑定逻辑 |

广告补丁共过滤 **70 个明确广告位**，修改 **73 个既有方法**，新增 3 个辅助类。混合使用的业务配置、导航入口和未知广告位键采用保留策略，并对已确认的广告展示入口单独处理。

## 定位修复

原应用在部分定位回调中，将高德返回的任意定位错误都提示为“获取定位权限失败”；该提示不代表系统权限一定被拒绝。无有效缓存时，应用默认使用北京坐标 `39.903179, 116.397755` 和行政区码 `110101`，定位失败后不会更新它们。

高德 Key 绑定包名与签名 SHA-1，重新签名可能导致鉴权失败；本次没有取得手机运行日志，尚不能确定实际错误码。绑定规则见 [高德官方 Key 说明](https://developer.amap.com/api/android-location-sdk/guide/create-project/get-key/)。

定位补丁保留原高德定位；失败或 6 秒未返回时，在已有系统定位权限下尝试 Android GPS、网络等位置提供器。仅使用真实位置，校验缓存时效和模拟位置，转换到应用使用的坐标系；请求总超时 25 秒，完成或页面销毁后清理监听器。

系统定位结果通过系统 Geocoder 查询地址，最多等待 3 秒。取得新坐标时更新位置缓存，防止部分旧回调只改经纬度、仍保留北京地址和区码；原误导提示改为显示实际错误类别与错误码。补丁未修改高德 Key、SDK 鉴权或系统权限规则。

**限制：**系统逆地理服务不可用时，可能只有坐标、暂时没有地址。系统地址不包含可靠的高德行政区码，补丁会留空，依赖行政区码的天气或城市服务仍需复测。高德地图、POI 检索等独立服务的签名鉴权不由系统定位补丁解决。

定位辅助代码通过 10 组离线 JVM 行为测试，覆盖成功、错误、超时、权限缺失、取消、缓存时效、模拟位置拒绝及缓存同步。测试使用 Android/高德替身，不代表真机定位已恢复。可重复运行：

```powershell
python ".\补丁文件\test_location.py"
```

测试输出保存在 `.analysis/location-tests`。修复包已完成 DEX 组装、仅两个目标 DEX 变更的条目校验及签名验证，仍需手机实际定位复测。

## 对正常功能的处理

- 保留登录、鉴权、支付、订单和设备操作的主体逻辑。
- 保留商城、积分页面等共用的配置接口，不整体屏蔽 `slot/get`。
- 保留 SDK 初始化中与业务共用的部分，未删除整个 SDK 包或原生库。
- 图片弹窗的关闭回调异步执行，避免弹窗队列递归或卡住。
- 激励广告按“广告不可用”处理，**不伪造观看完成、积分奖励或支付成功**。依赖看广告领奖、广告抵扣的选项可能提示不可用。

## 安装与更新

本版本使用本地测试证书重新签名，与官方原版签名不同，不能作为原版的普通覆盖更新安装。首次替换原版时，需要先处理旧版安装；卸载会清除应用本地数据，安装后可能需要重新登录。

后续更新本修改版时，应继续使用本次本地签名证书。同样，切回官方原版通常需要先卸载本修改版。Android 的签名规则见 [官方应用签名说明](https://developer.android.com/studio/publish/app-signing)。

定位修复测试包复用首版去广告包的本地证书，可以覆盖首版修改包安装，保留应用数据。其他设备、系统版本、第三方登录或商城服务仍可能存在签名兼容性差异。后续服务端新增广告、网页内动态广告以及其他 App 版本不在本次验证范围内。

## 构建与校验记录

- 原 APK 保留，另行输出修改版。
- 打包时，仅替换原包中的 `classes4.dex` 和 `classes5.dex`；签名另行生成。
- 打包校验确认原有 Manifest、资源、原生库和其余 DEX 内容保持一致。
- DEX 重新组装通过，APK Signature Scheme v2、v3 校验通过。
- 未压缩原生库按 16 KiB 对齐。
- 真机使用结果由用户确认；本目录未记录逐功能、逐设备的完整回归用例。

原 APK SHA-256：

```text
68cb64ecbe15e0daf26bfbf7b0fc05d72850a5d5a068ed50565e73d923c97024
```

## 目录结构

补丁源码集中放在 [补丁文件](补丁文件/) 中。`.analysis` 是脚本生成的本地工作目录，已被 `.gitignore` 忽略，不需要从 GitHub 下载，也不需要提交。

```text
项目根目录/
├── base.apk                        # 原始 1.139.0 APK
├── README.md
├── 广告定位报告.md
├── 补丁文件/                       # 可复用的补丁与构建脚本
│   ├── prepare_analysis.py         # 下载工具并生成 .analysis
│   ├── build_apk.py                # 应用补丁、构建、签名和校验
│   ├── patch_config.py
│   ├── patch_sdk.py
│   ├── patch_ui.py
│   ├── patch_location.py          # 定位回退、缓存与错误提示修复
│   ├── test_location.py           # 可复现的离线定位行为测试
│   ├── location-src/              # 定位辅助类的 Java 源码
│   ├── location-stubs/            # 仅编译时用的 API 声明，不打包
│   ├── package_apk.py
│   └── DecompileApp.java           # 可选的 Java 源码导出工具
├── .analysis/                      # 自动生成
│   ├── tools/                     # Apktool、签名工具、Android API、D8；可选 JADX
│   ├── decompiled/                # 使用 --decompile 时生成
│   └── noads/
│       ├── decoded-v2/             # smali 工作副本及 DEX 构建产物
│       ├── config-backup/          # 以下三个目录保留修改前的 smali
│       ├── sdk-backup/
│       ├── ui-backup/
│       ├── location-backup/        # 定位补丁应用前的 smali
│       ├── location-build/         # Java/D8 编译产物与日志
│       ├── local-signing.p12       # 本地签名私钥，首次构建生成
│       └── local-signing.pass      # 对应密码文件
└── output/                         # 签名后的 APK
```

## 从原始 APK 生成 .analysis

以下命令在 **Windows PowerShell、项目根目录** 中运行。准备 Python 3.10 或更高版本、JDK 21，并将 `python`、`java`、`javac`、`keytool` 加入 PATH；本项目使用 Python 3.14.6 和 JDK 21 验证。无需安装额外的 Python 包。

```powershell
python --version
java -version
javac -version
keytool -help
```

1. 将原始安装包放到项目根目录，命名为 `base.apk`；不存在时也接受本地文件名 `gpsh.apk`。脚本会核对上文的原包 SHA-256，只接受本次分析的 1.139.0 原包。
2. 执行准备脚本：

   ```powershell
   python ".\补丁文件\prepare_analysis.py"
   ```

脚本自动创建 `.analysis/tools` 和 `.analysis/noads`，从官方 GitHub Releases 下载 Apktool 3.0.3、uber-apk-signer 1.3.0，并从 Google 的仓库下载 Android 35 API 声明包、R8/D8 8.7.18。所有文件均校验固定 SHA-256，签名工具 `apksigner.jar` 从 uber-apk-signer 提取。然后生成精简输入包并用 Apktool 解码，得到 `.analysis/noads/decoded-v2`。这些文件已经足够应用补丁和重新构建 APK。

精简输入包只包含 Manifest、资源表、`classes4.dex`、`classes5.dex`，并将原包中较小的 `classes2.dex` 作为 `classes.dex` 占位，以便 Apktool 识别多 DEX。**占位 DEX 仅用于解码；最终打包只替换原包的 `classes4.dex`、`classes5.dex`。**

如需查看 Java 源码和资源，额外运行：

```powershell
python ".\补丁文件\prepare_analysis.py" --decompile
```

这会下载 JADX 1.5.6，将资源导出到 `.analysis/decompiled/resources`，将 `com.qiekj.user`、`com.qiekeji.pgad` 和 `com.qiekj.App` 的 Java 源码导出到 `.analysis/decompiled/sources`。JADX 对部分复杂方法可能报告反编译错误，详情见 `.analysis/decompile-app.log`；补丁直接处理 smali，不依赖这些 Java 文件。

首次运行需要能访问 GitHub 和 Google 工具仓库，后续会校验并复用工具缓存。下载失败时，可按 [准备脚本中的下载地址](补丁文件/prepare_analysis.py) 手动下载到 `.analysis/tools`，保持文件名一致后重试；Android API 的 Gitiles 地址返回 Base64 文本，需要先解码成 `android-35.jar`。已有完整的 `decoded-v2` 会被保留；若解码中断，先将不完整的目录改名留存，再重跑准备命令。希望从头重建时，可先将整个 `.analysis` 改名留存，再重新运行。

## 应用补丁并生成 APK

准备完成后执行：

```powershell
python ".\补丁文件\build_apk.py"
```

脚本依次应用配置、SDK、UI、定位补丁，编译定位 Java 辅助类并合入 `classes4.dex`，重新组装 DEX，替换原包中的两个目标 DEX，再生成或复用本地签名证书，签名并验证 APK。默认输出 `output/base-noads-1.139.0.apk`。仅编译时使用的 API 声明不会进入 APK。

重复构建时，会先校验并恢复上一轮定位补丁覆盖的文件，再按相同顺序应用各层补丁，避免与 UI 补丁的完整性校验冲突。请使用 `build_apk.py` 串联执行。

**已有同名 APK 时脚本会停止，避免覆盖。** 需要再次构建时指定新文件名：

```powershell
python ".\补丁文件\build_apk.py" --output "output/base-noads-1.139.0-rebuild.apk"
```

自己首次构建会生成新的签名证书，因此 APK 的 SHA-256 和证书可能与上面提供的成品不同。后续构建会复用 `.analysis/noads/local-signing.p12` 和 `local-signing.pass`；需要同签名覆盖更新时，请妥善保留这两个文件。重新生成 `.analysis` 后，应在构建前恢复这两个文件以继续使用原证书。不要将私钥或密码文件上传到 GitHub。

## 补丁文件与本地记录

| 文件 | 用途 |
| --- | --- |
| [prepare_analysis.py](补丁文件/prepare_analysis.py) | 校验原包、准备工具、生成解码目录；可选导出 Java 源码 |
| [build_apk.py](补丁文件/build_apk.py) | 串联补丁、DEX 构建、打包、签名和验证 |
| [patch_config.py](补丁文件/patch_config.py) | 从 smali 读取广告位定义，应用 70 个广告位过滤规则 |
| [patch_sdk.py](补丁文件/patch_sdk.py) | SDK 加载、展示及失败回调补丁 |
| [patch_ui.py](补丁文件/patch_ui.py) | 开屏、页面广告、扫码及推广卡片补丁 |
| [patch_location.py](补丁文件/patch_location.py) | 为 20 个应用类中的 24 处定位客户端接入系统定位回退，修复缓存与错误文案 |
| [location-src](补丁文件/location-src/) | 系统定位、地址解析、缓存同步的可编辑 Java 源码 |
| [location-stubs](补丁文件/location-stubs/) | 与原 APK 方法签名匹配的编译声明，不包含 SDK 实现、不打入 APK |
| [test_location.py](补丁文件/test_location.py) | 运行实际定位辅助源码的 10 组离线行为测试 |
| [package_apk.py](补丁文件/package_apk.py) | 替换目标 DEX、对齐并生成未签名 APK |
| [DecompileApp.java](补丁文件/DecompileApp.java) | 导出指定应用包的 Java 源码，供阅读分析 |

构建时自动在 `.analysis/noads` 生成以下记录；这些是本地生成文件，GitHub 仓库中不提供对应链接：

| 文件 | 内容 |
| --- | --- |
| `config-method-changes.json` | 广告位过滤和保留规则 |
| `sdk-changes.json` | SDK 方法级变更记录 |
| `ui-changes.json` | 页面方法级变更记录 |
| `location-changes.json` | 定位补丁的修改前后哈希、客户端数和辅助类清单 |
| `package-verification.json` | 原包条目与替换结果的哈希记录 |
| `signature-verification.txt` | 修改版 APK 的签名验证结果 |
| `decode-v2.log`、`build.log`、`signing.log` | 解码、组装、签名日志 |

补丁针对上文指定的原包，不能直接套用到其他版本 APK。补丁源码目录应保持在项目根目录的 `补丁文件` 下；脚本按自身位置定位 `.analysis`，不依赖当前终端的绝对路径。
