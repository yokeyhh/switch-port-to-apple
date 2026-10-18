# Switch → Apple 原生移植（OpenCode 技能）

一个 [OpenCode](https://opencode.ai) 技能，记录**如何让 Nintendo Switch（Horizon 系统，aarch64）游戏在
Apple 芯片上原生运行**——不做 CPU 模拟——做法是：

1. 离线改写**只有** Apple 内核跑不了的那几条指令；
2. 用一个小的**宿主**装载改写后的映像，顶替 Horizon 的角色；
3. **HLE**（高级模拟）内核系统调用与 SDK 系统服务；
4. **NVN → Metal** 翻译（游戏的着色器离线翻译好）；
5. 再用平台外壳（macOS `.app` / iOS `.app`）包起来。

内容以 [**d2r-mobile**](https://github.com/jhaizhou-ops/d2r-mobile) 项目（《暗黑破坏神 II：狱火重生》
Switch 版，MIT）为依据，并按**可复用到其它游戏**来写：包含逐文件的「引擎 vs 与本作绑定」复用对照表，
以及开始之前必须先关掉的本作专属默认项清单。

英文版：[README.md](README.md)

## 内容

| 文件 | 覆盖 |
|---|---|
| `SKILL.md` | 打法：这套方法什么时候适用、五层模型、适配新游戏的步骤 |
| `references/architecture.md` | 五层详解——改写表、装载器、HLE 宏、NVN→Metal、着色器翻译链、iOS 外壳 |
| `references/code-reuse-map.md` | 每个目录/文件按 **ENGINE（引擎）/ ADAPT（需适配）/ TITLE（本作专属）** 分类 |
| `references/build-pipeline.md` | 工作区准备、Mac/iOS 构建、产物、签名、往设备推游戏数据 |
| `references/pitfalls.md` | AAPCS64 与 Switch 的 ABI 差异、无 JIT 的签名规则、BC 纹理、版本漂移、着色器链的静默错误等 |
| `references/shader-banks.md` | `BNSH` 着色器包：怎么切出 NVN 程序，以及 `c1`（`BRX` 跳转表）的布局、提取与校验 |

`references/shader-banks.zh-CN.md` 是同一篇的中文版。

## v2 / v3 新增

- **v3 —— 可运行的工具。** `scripts/` 不再只是文档，而是整条流水线：切包、生成作业、
  `c1` 抽取/校验、SPIR-V → metallib、反射统计。不含任何游戏数据，全部跑在你自己那份文件上。
- **v2 —— D2R 之外的着色器包。** 面向用 `BNSH` 包（`nn::gfx`）发着色器、而不是已经切好
  单程序包的作品——怎么把 NVN 程序切出来、`c1`（`BRX` 跳转表）到底在哪（代码段末尾）、
  怎么不靠猜地定位、怎么用四条独立校验确认它，以及为什么翻译器必须带 `debug: true`
  （AST 优化器会让这类程序里约 43% 翻不出来）。中英双版。

## 工具

`switch-port-to-apple/scripts/` 是上面那篇参考的可运行配套。需要 Python 3.9+，不用第三方库；
`shader_chain.py` 另外要 `spirv-cross` 和 Xcode 的 Metal 工具链。

| 脚本 | 作用 |
|---|---|
| `bnsh.py` | 读 `BNSH` 包、列程序、导出成 `<bank>.<idx>.<stage>.code` |
| `dmsh_jobs.py` | 生成 `dmsh_xlate` 的作业 JSON（带不带 `c1` 都行） |
| `c1_extract.py` | 定位 `BRX` 跳转表的 run，输出补好基址的 `c1` blob |
| `c1_verify.py` | 四条独立校验（有失败则退出码非 0） |
| `shader_chain.py` | SPIR-V → MSL → `.metallib`（或 `--msl-only`） |
| `refl_stats.py` | 汇总 `*.res.json` 反射，给出槽位/格式/贴图统计 |

```sh
scripts/bnsh.py dump  shader.bnsh work/code
scripts/dmsh_jobs.py  work/code --out work/jobs_noc1.json
#   先翻译一遍，再从 note 里读 'c1 读越界'：
scripts/c1_extract.py work/out_noc1 --code work/code --out work/c1.json --junk-out work/junk.json
scripts/c1_verify.py  work/c1.json --code work/code --notes work/out_noc1
scripts/dmsh_jobs.py  work/code --c1 work/c1.json --out work/jobs_c1.json
#   再翻译一遍，然后：
scripts/shader_chain.py work/out_c1 work/mlib --jobs 10
```

完整顺序（含第二轮抽取）见 `switch-port-to-apple/scripts/README.md`。

## 安装

### 1. 拷进 OpenCode 技能目录（全局）

```sh
mkdir -p ~/.config/opencode/skills
cp -R switch-port-to-apple ~/.config/opencode/skills/
```

之后用 `@switch-port-to-apple` 提及它，或在任务匹配时由 agent 自动调用。

### 2. 作为 HTTP 目录源接入

在任意 `opencode.jsonc` 里把本仓加为技能源：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "skills": [
    "https://raw.githubusercontent.com/yokeyhh/switch-port-to-apple/main/"
  ]
}
```

OpenCode 会读取该基址下的 `index.json`，再从 `<基址>/<技能名>/<文件>` 下载各文件。

### 3. 克隆

```sh
git clone https://github.com/yokeyhh/switch-port-to-apple.git
```

## 范围与实话

- 本技能是**文档 + 少量通用工具**，不含**任何**游戏代码、美术、文本、数据表或着色器，也不下载任何
  这些内容。它描述的一切都从**你自己合法拥有**的游戏文件、在你自己的机器上生成。
- 这套方法的前提是源程序与 Apple 芯片**同为 arm64** 指令集、且图形接口是 **NVN**；
  非 arm64 的源（那些需要模拟）**不适用**。
- 它是 d2r-mobile 方法的知识沉淀。代码的权威来源是
  [d2r-mobile 仓库](https://github.com/jhaizhou-ops/d2r-mobile)。

## 许可

MIT——见 [LICENSE](LICENSE)。
