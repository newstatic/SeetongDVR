package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"seetong-dvr/internal/server"

	"github.com/kataras/iris/v12"
)

func main() {
	port := flag.Int("port", 8091, "Server port")
	dvrPath := flag.String("path", "/Volumes/DVR-2T/Seetong/Stream", "DVR base path")
	webPath := flag.String("web", "./web/dist", "Web static files path")
	flag.Parse()

	fmt.Println("============================================================")
	fmt.Println("天视通 DVR Web 服务器 (Go)")
	fmt.Println("============================================================")
	fmt.Printf("DVR 路径: %s\n", *dvrPath)
	fmt.Printf("监听地址: http://localhost:%d\n", *port)
	fmt.Println("============================================================")

	// 创建 DVR 服务器（不立即加载）
	dvr := server.NewDVRServer(*dvrPath)
	defer dvr.Close()

	// 检查路径是否存在，存在则自动加载
	if _, err := os.Stat(*dvrPath); err == nil {
		fmt.Println("正在加载 DVR 数据...")
		if err := dvr.Load(); err != nil {
			fmt.Printf("警告: 加载失败: %v\n", err)
			fmt.Println("服务器将继续运行，请通过 API 设置正确的路径")
		} else {
			// 后台构建 VPS 缓存
			go func() {
				fmt.Println("正在构建 VPS 缓存...")
				if err := dvr.BuildVPSCache(); err != nil {
					fmt.Printf("警告: 构建 VPS 缓存失败: %v\n", err)
				}
			}()
		}
	} else {
		fmt.Printf("警告: DVR 路径不存在: %s\n", *dvrPath)
		fmt.Println("服务器将继续运行，请通过 API 或前端设置正确的路径")
	}

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

	// 静态文件
	if _, err := os.Stat(*webPath); err == nil {
		app.HandleDir("/", iris.Dir(*webPath), iris.DirOptions{
			IndexName: "index.html",
			SPA:       true,
		})
		fmt.Printf("静态文件目录: %s\n", *webPath)
	} else {
		fmt.Printf("警告: 静态文件目录不存在: %s\n", *webPath)
	}

	// 优雅关闭
	go func() {
		ch := make(chan os.Signal, 1)
		signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
		<-ch
		fmt.Println("\n正在关闭...")
		app.Shutdown(nil)
	}()

	// 启动服务器
	fmt.Printf("\n服务器已启动: http://localhost:%d\n", *port)
	if err := app.Listen(fmt.Sprintf(":%d", *port)); err != nil {
		fmt.Printf("服务器错误: %v\n", err)
	}
}
