# best_result
用户要最高得分、最好成绩、最高准确率或当前指标时，调用 best_result 一次，hint 填用户的原话。
「得分」不是日志里必须有一列叫 score。准确率、mAP、BLEU、F1 都算评估成绩，以 headline.metric 的原名为准。
表格主列用 headline 的 metric、best、best_round。others 里的其余指标可以附上。
higher_is_better 为 false 时，最好成绩是最小值，不要写成最高。
没有评估行就说明日志里还没有 val/test/eval 记录，不要改用 log_diagnose 的尾部 loss 冒充得分。
