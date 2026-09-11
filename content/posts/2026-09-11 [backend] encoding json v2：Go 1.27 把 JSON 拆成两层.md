+++
date = '2026-09-11T10:09:22+08:00'
draft = false
title = 'encoding/json/v2：Go 1.27 把 JSON 拆成两层'
author = 'JekYUlll'
lastmod = '2026-09-11T10:09:22+08:00'
tags = ['go', 'json', 'jsontext']
categories = ['backend']
+++

encoding/json 在标准库里待了十几年，攒下一批改不掉的毛病：字段名匹配大小写不敏感、重复键照单全收、字符串里的无效 UTF-8 被悄悄替换成 U+FFFD。想在原包上修，就要破坏兼容，Go 1 的承诺不允许。

Go 团队选了最重的方案：另写一套 v2，然后让 v1 跑在它上面。2026 年 8 月发布的 Go 1.27 里，这件事落地了。encoding/json/v2 和 encoding/json/jsontext 两个包默认可用（1.25 刚引入时还要设 GOEXPERIMENT=jsonv2），encoding/json 本身也换成了新引擎实现。

进标准库前，它先在 go-json-experiment 仓库里以原型形态开发；2025 年 8 月以 GOEXPERIMENT 实验形式进来后，API 在实验期还改过几轮：`format` 字段标签被删、`inline` 改名成 `embed`、jsontext 的数字 token 访问器改成返回 error。到 1.27，形态才固定下来。

## v1 换引擎，行为照旧

升级到 1.27 的项目不需要改代码，encoding/json 的编解码行为保持不变，官方明确会变的是报错文本：错误信息的具体措辞可能不同，所以断言过错误字符串的测试要留意。

v1 也不能直接删：标准库外还有大量程序在 import 它。Go 1.27 的处理方式是让 v1 变成套在 v2 引擎上的兼容实现：行为照旧、底层换新，只有错误文本不完全一致。

真遇到兼容问题，可以设 GOEXPERIMENT=nojsonv2 让 encoding/json 退回旧实现。我用 1.27.1 实测了一下，这是个只给存量代码用的逃生舱：一旦代码里 import 了 json/v2 或 jsontext，加上这个开关会直接编译失败（两个包的 build 约束被排除了）。官方也说明这个开关将来会移除。

## 为什么拆成 jsontext 和 json

v2 最大的结构变化是把职责切成两层：

- jsontext：语法层。处理 token 和原始 JSON 值，不依赖反射，不认识任何 Go 类型，Encoder/Decoder 是一个流式状态机。
- json/v2：语义层。负责 Go 值和 JSON 之间的映射，构建在 jsontext 之上。

术语也跟着分开：语法层的读写叫 encode/decode，语义层的映射叫 marshal/unmarshal。语义层还顺手补了流式 API：`MarshalWrite`、`UnmarshalRead` 直接对着 io.Writer 和 io.Reader 工作，HTTP handler 里一句 `jsonv2.UnmarshalRead(r.Body, &req)`，省掉中间的 []byte 缓冲。这不是洁癖，好处在流式场景里很直接。以前读 NDJSON、或者一个流里连着好几个 JSON 值，要么用 Scanner 手拼，要么拿 Decoder.Token 自己维护状态；现在 ReadValue 一次返回一个完整的 JSON 值，而且不经过 Go 类型系统：

```go
package main

import (
	"encoding/json/jsontext"
	"fmt"
	"strings"
)

func main() {
	r := strings.NewReader("{\"age\":1}\n{\"age\":2}\n[1,2]\n")
	dec := jsontext.NewDecoder(r)
	for {
		val, err := dec.ReadValue()
		if err != nil {
			break // io.EOF 表示读完
		}
		fmt.Printf("offset=%d kind=%c value=%s\n", dec.InputOffset(), val.Kind(), val)
	}
}
```

```
offset=9 kind={ value={"age":1}
offset=19 kind={ value={"age":2}
offset=25 kind=[ value=[1,2]
```

kind 那一列是值的类型：`{` 表示对象，`[` 表示数组。有个细节要记住，val 是从解码器内部缓冲里切出来的，下一次读取就会失效，想留住它就先 `val.Clone()`。

## 换到 v2，这些默认值不一样了

把 import 从 encoding/json 换成 encoding/json/v2，函数签名基本不用动，Marshal 和 Unmarshal 可以直接平移。要小心的是默认行为换了一批：v2 换了更严格、更讲互操作的一套默认值。挑几个最容易踩的：

- 字段名匹配默认大小写敏感。`{"NAME":"gopher"}` 在 v1 里能匹配到 Name 字段，v2 直接忽略。要旧行为加 `jsonv2.MatchCaseInsensitiveNames(true)`，也可以给单个字段打 `case:ignore` 标签。
- 重复键直接报错：`jsontext: duplicate object member name "age"`。v1 是后者覆盖前者，v2 拒绝解析；放行要显式传 `jsontext.AllowDuplicateNames(true)`。
- 字符串里的无效 UTF-8 报错，不再静默替换（`jsontext.AllowInvalidUTF8` 可以放回去）。
- nil slice 和 nil map 分别编码成 `[]` 和 `{}`，不是 `null`；退回用 `FormatNilSliceAsNull`、`FormatNilMapAsNull`。
- omitempty 语义变了：按“编码结果是不是 JSON 空值（null、空字符串、空对象、空数组）”来省略。false 和 0 这类不再算空，int 字段的 0 会被保留；这种字段官方建议改用 `omitzero`。
- map 序列化顺序不再保证。v1 一直按 key 排序，v2 按 map 迭代顺序，每次输出都可能不同；要稳定就传 `jsonv2.Deterministic(true)`。
- HTML 转义默认关闭，`<`、`>`、`&` 原样输出，不再是 `\u003c` 这种写法；要旧行为用 `jsontext.EscapeForHTML`。
- 定长 `[N]byte` 数组的表示从 JSON 数字数组改成 base64 字符串（`FormatByteArrayAsArray` 退回）。
- time.Duration 默认不可序列化，直接报运行时错误；v1 把它当纳秒整数处理，要保留用 `FormatDurationAsNano`。

写个程序把两边跑一遍，差异一目了然。下面的代码和输出都在 Go 1.27.1、linux/amd64 上实际跑过：

```go
package main

import (
	"encoding/json"
	jsonv2 "encoding/json/v2"
	"fmt"
)

type User struct {
	Name    string   `json:"name"`
	Age     int      `json:"age"`
	Hidden  int      `json:"hidden,omitempty"`
	HiddenZ int      `json:"hiddenz,omitzero"`
	Balance int64    `json:"balance,string"`
	Tags    []string `json:"tags"`
}

func main() {
	// 字段名匹配
	in := []byte(`{"NAME":"gopher","age":3}`)
	var v1u, v2u User
	json.Unmarshal(in, &v1u)
	jsonv2.Unmarshal(in, &v2u)
	fmt.Printf("v1: name=%q age=%d\n", v1u.Name, v1u.Age)
	fmt.Printf("v2: name=%q age=%d\n", v2u.Name, v2u.Age)

	// 重复键
	dup := []byte(`{"age":1,"age":2}`)
	fmt.Println("v2 dup err:", jsonv2.Unmarshal(dup, new(User)))

	// 类型错误
	fmt.Println("v1 type err:", json.Unmarshal([]byte(`{"age":"x"}`), new(User)))
	fmt.Println("v2 type err:", jsonv2.Unmarshal([]byte(`{"age":"x"}`), new(User)))

	// 序列化默认值
	u := User{Name: "a<b&>c", Age: 30, Balance: 4096}
	b1, _ := json.Marshal(u)
	b2, _ := jsonv2.Marshal(u)
	fmt.Println("v1:", string(b1))
	fmt.Println("v2:", string(b2))
}
```

```
v1: name="gopher" age=3
v2: name="" age=3
v2 dup err: jsontext: duplicate object member name "age"
v1 type err: json: cannot unmarshal string into Go struct field User.age of type int
v2 type err: json: cannot unmarshal JSON string into Go int within "/age"
v1: {"name":"a\u003cb\u0026\u003ec","age":30,"balance":"4096","tags":null}
v2: {"name":"a<b&>c","age":30,"hidden":0,"balance":"4096","tags":[]}
```

第一组看字段匹配，v1 认出了 NAME，v2 没认。第二组重复键，v2 直接指名道姓。第三组是报错信息的变化：v2 除了 struct field，还带上了 JSON 指针 `within "/age"`，嵌套深了也能定位到具体位置。最后一组是序列化，转义、nil slice、omitempty（值为 0 的 hidden 被保留）、omitzero（hiddenz 两边都省略）一次看全。

v1 和 v2 的行为差异官方一共列了 16 条，每一条都有对应选项可以退回 v1 行为，完整清单在 encoding/json 包的 Migrating to v2 文档里。

迁移前建议先核对和外部系统的约定：下游如果依赖 `null` 和 `[]` 的区别，或者依赖某个字段被省略，这类边界在迁移时最容易出问题。

## 迁移不用推倒重来

官方给的迁移路径分三档：

1. 全量切换。把 import 换掉跑测试，出问题按报错查对应选项。
2. 逐选项切换。先用 `jsonv2.Marshal(v, jsonv1.DefaultOptionsV1())` 把行为锚回 v1；它背后是 21 个兼容选项，从左到右叠加、后面的覆盖前面的，可以一次只打开一条 v2 行为，慢慢确认。
3. 线上双跑。用 github.com/go-json-experiment/jsonsplit 同时按 v1 和 v2 序列化，比对差异并上报，返回值仍用 v1；它还能自动定位到是哪条选项造成的差异。双跑大约让序列化成本翻倍，可以按调用比例采样来控制开销。

还有个容易忽略的互操作细节：给类型实现 v2 的 MarshalerTo 接口之后，走 v1 的 encoding/json.Marshal 也会命中这个实现。两个包不是割裂的，库作者可以按新接口写，老调用方照常工作。

性能方面，官方口径是 marshal 两代持平、unmarshal 快得多。我在本机（Go 1.27.1，1000 条结构体的 slice）跑了个小对比：marshal 基本持平，unmarshal v2 快了 15% 上下。

## 升级到 1.27 之后

先跑一遍全量测试，重点看错误字符串断言。新项目直接 import encoding/json/v2；日志、消息流这类逐条读 JSON 的场景，用 jsontext 的 ReadValue 比自己拼状态机干净得多。存量服务想验证兼容性，用 DefaultOptionsV1 打底或 jsonsplit 双跑，确认稳定后再逐条打开 v2 默认值。

Go 团队的态度也很明确：v1 会永远支持，但所有新的 JSON 用法都应该走 v2。

## 参考

- Go 1.27 Release Notes: https://go.dev/doc/go1.27#jsonv2
- Go 1.25 Release Notes（实验引入）: https://go.dev/doc/go1.25#json_v2
- encoding/json/v2 Migration Guide: https://go.dev/doc/jsonv2-migration
- 提案 issue #71497: https://go.dev/issue/71497
- jsonbench（性能对比）: https://github.com/go-json-experiment/jsonbench
- go-json-experiment/json（原型仓库）: https://github.com/go-json-experiment/json
