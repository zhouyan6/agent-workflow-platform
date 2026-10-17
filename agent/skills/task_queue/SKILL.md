# task_queue
用户问正在跑什么、队列里有没有任务时，调用 task_queue。
默认看 active：工作台里 pending 与 running，以及本机 GPU 上由 Cursor、SSH、终端直接启动、没有经过工作台提交的进程。
source=workbench 是工作台提交，source=external 是终端或 Cursor 直接启动。external 的 id 是负数，等于 -pid。
用户要历史记录时传 status=all。
回答时写出来源、名称、GPU 和 pid。工作台队列为空但有 external 任务时，说明这些任务正在 GPU 上跑，不要说没有任务或工作台空闲。
只根据返回的任务回答，不要猜测退出码。
