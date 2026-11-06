# Attribution

The original InspectCoder CJ implementation is distributed under the MIT license in `LICENSE`.
The attribution and third-party references below are preserved separately from that license grant.

This implementation is inspired by *InspectCoder: Dynamic Analysis-Driven Self Repair through Interactive LLM-Debugger Collaboration*, by Yunkun Wang, Yue Zhang, Guochang Li, Chen Zhi, Binhua Li, Fei Huang, Yongbin Li and Shuiguang Deng, PACMPL 10 (OOPSLA1), article 130 (April 2026), [DOI 10.1145/3798238](https://doi.org/10.1145/3798238).
The supplied paper states a Creative Commons Attribution 4.0 International license.
The method has been adapted from Python/PDB to native Cangjie/CJDB, with a new implementation, JSON protocol, model transports and executable test contract.

The [InspectCoder reference implementation](https://github.com/greenlight2000/InspectCoder/) at commit `f64730e57b83f74aceb22cbdab6d9bd2cdb9bb0e` informed the design.
No upstream source files or benchmark datasets are included in this delivery.
See [paper mapping](https://github.com/mtghorbani/inspectcoder-cj/blob/main/docs/paper-mapping.md) for the specific correspondences and differences.

Native debugger behavior is informed by the [official Cangjie CJDB manual](https://docs.cangjie-lang.cn/en/docs/1.0.0/tools/tools/cjdb_manual_cjnative.html).
Model transports follow the [official structured outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) and [noninteractive Codex documentation](https://developers.openai.com/codex/noninteractive), checked against the installed CLI's live help.
