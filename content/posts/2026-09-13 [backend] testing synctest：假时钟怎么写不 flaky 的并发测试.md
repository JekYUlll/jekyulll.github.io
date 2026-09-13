+++
date = '2026-09-13T10:05:18+08:00'
draft = false
title = 'testing/synctest：假时钟怎么写不 flaky 的并发测试'
author = 'JekYUlll'
lastmod = '2026-09-13T10:05:18+08:00'
tags = ['go', 'concurrency', 'testing', 'synctest']
categories = ['backend']
+++

用真实时间写并发测试，慢和飘只能二选一：断言等短了叫 flaky，等长了叫慢，没有第三种结果。Damien Neil 在 Go 博客里把这件事说得很直白，net/http 包里就攒着一批这样的测试，每天在 CI 上消耗工程师的耐心，这也是 testing/synctest 诞生的直接动机。

老路不是没人走过。注入假时钟，意味着被测代码放弃标准库的 `time`，换成自定义接口，从这一层开始改造整条调用链；手写探针，则要给每个包单独埋一套"现在安静了吗"的检测逻辑，引用的第三方并发代码根本覆盖不到。Damien Neil 把这两种办法在 net/http 上都试过，HTTP/2 服务端把他挡了回来，不重写就没法准确定义"安静"。

Go 1.24 把它作为实验包发出来，1.25 转正进标准库，1.27 又补上 `synctest.Sleep` 和 `httptest.NewTestServer` 两件顺手工具。核心思路一句话：把测试关进一个隔离的 bubble，bubble 里的时间由假时钟驱动，只在所有 goroutine 都停稳时才前进。不用改业务代码，也不用真的等。

更早的原型更野：用 `runtime.Stack` 抓全量 goroutine 栈文本，靠解析文本判断系统静没静。栈格式没有任何稳定性承诺，博客自己说这是个 terrible idea，然后承认它跑得意外地好。这个明摆着不该上生产的原型证明了方向可行，后面才有了运行时级别的正规实现。

## bubble 里没有真实时间

`synctest.Test(t, func(t *testing.T) { ... })` 开出一个 bubble。回调里启动的 goroutine、创建的 channel、timer、ticker 全部归属这个 bubble，`time` 包在里面对接假时钟，起点固定是 2000-01-01 00:00 UTC。时间不会自己走，一个 bubble 里所有 goroutine 都处于 durably blocked 状态时，才会触发三个动作之一：有 `Wait` 在等就唤醒它；没有就跳到下一个定时事件；再没有，就是死锁，Test 报失败。

bubble 里的 `*testing.T` 也是受限的，`T.Run`、`T.Parallel`、`T.Deadline` 不能调用，`T.Context()` 返回的 context 随 bubble 生命周期一起结束。

1.24 到 1.25 之间 API 改过一轮。入口从 `Run` 改名 `Test`，理由很实际：需要一个绑定 bubble 生命周期的 `*testing.T`，让 `t.Cleanup` 在 bubble 内部执行。同时修掉几个"durable 不彻底"的边角，bubble 里同时刻到期的 timer 改成随机顺序触发，专门抓那些依赖同刻顺序的测试。root goroutine，也就是 Test 回调本身，一返回时间就冻结，后面的死锁案例就靠这条规则解释。

## durable blocked 是唯一的同步标尺

durably blocked 的定义：goroutine 被阻塞，且只能被同一个 bubble 里的 goroutine 唤醒。官方清单不长：

- bubble 内创建的 channel 上的阻塞收发
- 每个 case 都是 bubble channel 的 select
- `sync.Cond.Wait`
- bubble 内调过 `Add` 的 `sync.WaitGroup.Wait`
- `time.Sleep`

反过来，锁 mutex、网络 I/O、系统调用都不算数，因为它们可能被 bubble 外的事件唤醒。这意味着测试要自足：别碰外部进程，别连真实网络，也别等 bubble 外 goroutine 的消息。官方已经把 mutex 列进未来清单：也许哪天锁也能算持久阻塞，前提是锁必须在同一个 bubble 里释放。在那之前，测试里要共享状态，就用 channel 把变化传出来。

配套函数是 `synctest.Wait()`，阻塞到当前 bubble 里其他 goroutine 全部停稳。它解决的是最难写的否定断言：到此刻为止什么都没发生，不足以说明问题，你得知道它接下来会不会发生。Wait 返回时，系统要么已经做完，要么就停在原地等你推进。

## 给一个重试函数上假时钟

被测系统是一个带指数退避的重试函数：

```go
// Retry 以 base、2*base、4*base 的间隔重试 fn，
// 直到成功、用完 attempts 次机会，或者 ctx 超时。
func Retry(ctx context.Context, attempts int, base time.Duration, fn func(attempt int) error) error {
	var err error
	for i := 0; i < attempts; i++ {
		if err = fn(i); err == nil {
			return nil
		}
		if i == attempts-1 {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(base << i):
		}
	}
	return err
}
```

它直接用了 `time.After` 和 ctx 超时，放真实时间里测试就是典型受害者：要断言"700ms 后成功"，测试得真等 700ms 再加安全垫，断言只能写不等式；要断言"250ms 时超时"，等待得精确卡进缝隙，还得祈祷 CI 别抖。假时钟下，这些不等式全变成等式：

```go
func TestRetryReturnsAfterSuccess(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		start := time.Now()
		attempts := 0
		err := Retry(context.Background(), 5, 100*time.Millisecond, func(i int) error {
			attempts++
			if i < 3 {
				return errors.New("boom")
			}
			return nil
		})
		if err != nil {
			t.Fatalf("Retry: %v", err)
		}
		if attempts != 4 {
			t.Fatalf("attempts = %d, want 4", attempts)
		}
		if got := time.Since(start); got != 700*time.Millisecond {
			t.Fatalf("elapsed = %v, want 700ms", got)
		}
	})
}

func TestRetryStopsAtDeadline(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		ctx, cancel := context.WithTimeout(t.Context(), 250*time.Millisecond)
		defer cancel()

		start := time.Now()
		attempts := 0
		err := Retry(ctx, 10, 100*time.Millisecond, func(int) error {
			attempts++
			return errors.New("boom")
		})
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("Retry = %v, want DeadlineExceeded", err)
		}
		if attempts != 2 {
			t.Fatalf("attempts = %d, want 2", attempts)
		}
		if got := time.Since(start); got != 250*time.Millisecond {
			t.Fatalf("elapsed = %v, want 250ms", got)
		}
	})
}
```

第一个测试里，三次失败累计等待 100+200+400ms，第四次成功返回，总计恰好 700ms；断言用 `!=` 精确比较，差一毫秒都挂。第二个测试卡的是更刁的边界：第二次尝试后要等 200ms 退避，但 250ms 时 ctx 超时先到，函数必须原样返回 `DeadlineExceeded`，总耗时恰好 250ms。这些等式在真实时间下没人敢写，写了就是定时炸弹。而实际运行是：

```
$ go test -v -run TestRetry .
=== RUN   TestRetryReturnsAfterSuccess
--- PASS: TestRetryReturnsAfterSuccess (0.00s)
=== RUN   TestRetryStopsAtDeadline
--- PASS: TestRetryStopsAtDeadline (0.00s)
PASS
ok  	demo	0.003s
```

假时钟里过了 950ms，真实世界只花了 3ms；同一组测试加 `-race` 也全绿，bubble 和 race detector 能一起工作。

## 忘了 Stop 的 goroutine 会被点名

假时钟还有个副作用：bubble 里留着没退干净的后台 goroutine，测试会失败并点名，而不是悄悄泄漏。一个每 50ms 拉一次数据的 Poller，如果测试忘了调用它的 Stop：

```go
func TestPollerForgetStop(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		_ = StartPoller(func() {})
		// 忘了 p.Stop()：goroutine 留在 bubble 里等 ticker
	})
}
```

跑出来是这样：

```
--- FAIL: TestPollerForgetStop (0.00s)
panic: deadlock: main bubble goroutine has exited but blocked goroutines remain

goroutine 10 [select (durable), synctest bubble 1]:
demo.(*Poller).loop(...)
	/path/poller.go:25 +0xec
created by demo.StartPoller in goroutine 9
```

栈里那行 `[select (durable), synctest bubble 1]` 是 1.25 之后增强过的提示：这个 goroutine 在 bubble 1 里持久阻塞，而且永远等不到唤醒。修法就是补上 `p.Stop()`。真实世界里，漏 Stop 的 goroutine 会活到进程结束，平时根本看不见；bubble 把"测试要自足"从一句建议变成了会失败的规则。1.27 新加的 `synctest.Sleep(d)` 等价于 `time.Sleep(d)` 加 `Wait()`，测试中途想让系统先跑一会儿时很好用：

```go
func TestPollerRunsUntilStop(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		var ticks int
		p := StartPoller(func() { ticks++ })

		synctest.Sleep(160 * time.Millisecond) // 跑过 3 个 tick
		if ticks != 3 {
			t.Fatalf("ticks = %d, want 3", ticks)
		}

		p.Stop()
		p.Wait()
	})
}
```

## 假网络：httptest.NewTestServer

网络 I/O 不算持久阻塞，想测 HTTP 客户端和服务端，网络就得是假的。以前只有 `net.Pipe` 能凑合，还得手工塞进 Transport；1.27 的 `httptest.NewTestServer` 把内存网络和 server、client 打包好了：

```go
func TestSlowHandlerOnFakeNetwork(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		ts := httptest.NewTestServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			time.Sleep(time.Second) // 慢接口：真实环境里测试至少要干等 1 秒
			io.WriteString(w, "ok")
		}))

		start := time.Now()
		resp, err := ts.Client().Get("http://example.tld/")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("status = %d", resp.StatusCode)
		}
		if got := time.Since(start); got != time.Second {
			t.Fatalf("elapsed = %v, want exactly 1s", got)
		}
	})
}
```

handler 里的 `time.Sleep` 走假时钟，请求在真实世界瞬间完成，但测试里量到的时间就是整 1 秒。`Client()` 拿到的 client 不用手写 Transport，任何域名、https 请求都会路由到这个内存 server 上。注意 server 要建在 `synctest.Test` 的回调里，它的 accept 和 handler goroutine 才归 bubble 管。

## 边界

- mutex 不是持久阻塞，别拿它当测试的同步点，`Wait` 不等锁上阻塞的 goroutine。生产代码该用锁就用，测试的节奏靠 channel 和 Wait 对齐。
- 别跨 bubble 传 channel：bubble 内建的 channel 被外部操作会 panic；WaitGroup 一旦绑定 bubble，外部再 `Add` 直接 fatal。
- 1.25 起不开 `GOEXPERIMENT` 也能用；旧的 `Run` API 在 1.26 移除，别照抄 1.24 时代的示例。
- 博客里还有条通用建议：能把并发往调用链上层推就往上推。Cleanup 写成同步函数、让调用方自己 `go`，测试难度立刻降一个维度；synctest 是兜底，不是鼓励把异步埋得到处都是。
- 假时钟作用在 `time` 包这一层，第三方库的计时只要最终落到标准库 `time`，进 bubble 就自动跟着走；反过来，和 bubble 外的 goroutine、timer 打交道是这类测试最常见的翻车点，它们不受假时钟和 `Wait` 管辖。

## 参考

- Go Blog：Testing concurrent code with testing/synctest：https://go.dev/blog/synctest
- Go Blog：Testing Time (and other asynchronicities)：https://go.dev/blog/testing-time
- pkg.go.dev：testing/synctest 包文档：https://pkg.go.dev/testing/synctest
- Go 1.25 Release Notes（synctest 转正）：https://go.dev/doc/go1.25
- Go 1.27 Release Notes（synctest.Sleep、httptest.NewTestServer）：https://go.dev/doc/go1.27
- 提案 #67434：testing/synctest: new package for testing concurrent code：https://github.com/golang/go/issues/67434
