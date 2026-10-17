# repro_check
用户问这篇论文能不能跑、缺什么目录或命令时，调用 repro_check。
根据返回的 has_command、roots 和 missing_dirs 说明缺什么。不要声称目录存在，除非工具写了 exists=true。
