"""模块自己的模板维护入口，不启动 ERP，也不读取账号数据库。"""
import argparse
from pathlib import Path

from .tariff_store import import_template, read_current, read_version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rules-dir', type=Path, required=True, help='费率版本库目录')
    parser.add_argument('--platform', choices=('ozon', 'yandex'))
    parser.add_argument('--import-template', type=Path)
    parser.add_argument('--effective-date')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--list-tariffs', action='store_true')
    args = parser.parse_args()
    if args.list_tariffs:
        if args.import_template or args.preview or args.effective_date:
            parser.error('查看版本和导入请分别执行')
        for platform, version in read_current(args.rules_dir)['platforms'].items():
            pack = read_version(args.rules_dir, version)
            print(f'{platform}: {version}，{len(pack["content"]["rules"])} 条规则')
        return
    if not args.platform or not args.import_template:
        parser.error('导入需要 --platform 和 --import-template')
    result = import_template(args.import_template, args.platform, args.rules_dir, args.effective_date, args.preview)
    print(f'{result["status"]}；版本 {result["after_version"]}；差异报告：{result["report"]}')


if __name__ == '__main__':
    main()
