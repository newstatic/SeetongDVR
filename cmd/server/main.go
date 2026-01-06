package main

import (
	"embed"
	"flag"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"seetong-dvr/internal/seetong"
	"seetong-dvr/internal/server"

	"github.com/kataras/iris/v12"
)

//go:embed static/*
var staticFS embed.FS

func main() {
	port := flag.Int("port", 8000, "Server port")
	dvrPath := flag.String("path", "", "DVR base path (optional, can be set via web UI)")
	debug := flag.Bool("debug", false, "Enable debug logging")
	noBrowser := flag.Bool("no-browser", false, "Don't open browser automatically")
	flag.Parse()

	// 设置日志级别
	if *debug {
		seetong.SetDebugMode(true)
	}

	// 查找可用端口
	actualPort := findAvailablePort(*port)

	fmt.Println("============================================================")
	fmt.Println("天视通 DVR Web 播放器")
	fmt.Println("============================================================")
	if *dvrPath != "" {
		fmt.Printf("DVR 路径: %s\n", *dvrPath)
	}
	fmt.Printf("监听地址: http://localhost:%d\n", actualPort)
	fmt.Println("============================================================")

	// 创建 DVR 服务器
	dvr := server.NewDVRServer(*dvrPath)
	defer dvr.Close()

	// 创建 Iris 应用
	app := iris.New()
	app.Logger().SetLevel("warn")

	// CORS
	app.UseRouter(func(ctx iris.Context) {
		ctx.Header("Access-Control-Allow-Origin", "*")
		ctx.Header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		ctx.Header("Access-Control-Allow-Headers", "Content-Type")
		if ctx.Method() == "OPTIONS" {
			ctx.StatusCode(204)
			return
		}
		ctx.Next()
	})

	// 注册 API 路由
	handlers := server.NewHandlers(dvr)
	server.RegisterRoutes(app, handlers)

	// 嵌入的静态文件
	staticSub, err := fs.Sub(staticFS, "static")
	if err != nil {
		fmt.Printf("警告: 无法加载嵌入的静态文件: %v\n", err)
	} else {
		app.HandleDir("/", http.FS(staticSub), iris.DirOptions{
			IndexName: "index.html",
			SPA:       true,
		})
		fmt.Println("静态文件: 嵌入模式")
	}

	// 优雅关闭
	go func() {
		ch := make(chan os.Signal, 1)
		signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
		<-ch
		fmt.Println("\n正在关闭...")
		app.Shutdown(nil)
	}()

	// 自动打开浏览器
	if !*noBrowser {
		go func() {
			time.Sleep(500 * time.Millisecond)
			openBrowser(fmt.Sprintf("http://localhost:%d", actualPort))
		}()
	}

	// 启动服务器
	fmt.Printf("\n服务器已启动: http://localhost:%d\n", actualPort)
	if err := app.Listen(fmt.Sprintf(":%d", actualPort)); err != nil {
		fmt.Printf("服务器错误: %v\n", err)
	}
}

// findAvailablePort 查找可用端口，如果指定端口被占用则递增
func findAvailablePort(startPort int) int {
	for port := startPort; port < startPort+100; port++ {
		ln, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
		if err == nil {
			ln.Close()
			return port
		}
	}
	return startPort // 回退到原始端口
}

// openBrowser 打开默认浏览器
func openBrowser(url string) {
	var err error
	switch runtime.GOOS {
	case "darwin":
		err = exec.Command("open", url).Start()
	case "linux":
		err = exec.Command("xdg-open", url).Start()
	case "windows":
		err = exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
	}
	if err != nil {
		fmt.Printf("无法自动打开浏览器，请手动访问: %s\n", url)
	}
}
