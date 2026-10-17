# submit_task
用户明确要求提交一条实验命令时，调用 submit_task。
必须带上 name、command。有工作目录就填 workdir，需要固定 GPU 就填 gpus_pinned。
该操作等用户确认后才执行。确认前不要说任务已经进队列。
