# log_diagnose
用户要看训练是否异常、报错时，调用 log_diagnose。
问最高得分、准确率或最好成绩时不要用本技能，改用 best_result。
优先看 signals：NaN、OOM、Python异常、进程或 CUDA 错误。
结合 tail 说明现象和下一步建议。没有日志就直接说没有，不要编造曲线。
