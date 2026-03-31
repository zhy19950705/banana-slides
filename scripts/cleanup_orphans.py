#!/usr/bin/env python3
"""
清理孤立上传文件的临时脚本
用法:
  # dry-run（默认，仅列出）
  python cleanup_orphans.py

  # 实际删除
  python cleanup_orphans.py --delete
"""
import os
import re
import sys
import shutil
from pathlib import Path

# 添加 backend 到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app import create_app
from models import db, Project, ReferenceFile, UserTemplate


def human_size(size_bytes):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} TB'


def dir_size(path):
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def main():
    delete = '--delete' in sys.argv
    app = create_app()

    with app.app_context():
        upload_folder = Path(app.config['UPLOAD_FOLDER'])
        if not upload_folder.exists():
            print(f'Upload folder not found: {upload_folder}')
            return

        known_dirs = {'materials', 'mineru_files', 'reference_files', 'user-templates', 'editable_images', 'tmp'}
        total_size = 0
        total_count = 0

        # 1. 孤立项目目录
        print('\n=== 孤立项目目录 ===')
        for entry in sorted(upload_folder.iterdir()):
            if not entry.is_dir() or entry.name in known_dirs:
                continue
            if Project.query.filter_by(id=entry.name).first() is None:
                size = dir_size(entry)
                total_size += size
                total_count += 1
                print(f'  {entry.name}  {human_size(size)}')
                if delete:
                    shutil.rmtree(entry)
                    print(f'    -> 已删除')

        # 2. 孤立参考文件
        print('\n=== 孤立参考文件 ===')
        ref_dir = upload_folder / 'reference_files'
        if ref_dir.exists():
            db_paths = {r.file_path for r in ReferenceFile.query.with_entities(ReferenceFile.file_path).all()}
            for entry in sorted(ref_dir.iterdir()):
                if not entry.is_file():
                    continue
                relative = f'reference_files/{entry.name}'
                if relative not in db_paths:
                    size = entry.stat().st_size
                    total_size += size
                    total_count += 1
                    print(f'  {entry.name}  {human_size(size)}')
                    if delete:
                        entry.unlink()
                        print(f'    -> 已删除')

        # 3. 孤立 MinerU 目录
        print('\n=== 孤立 MinerU 目录 ===')
        mineru_dir = upload_folder / 'mineru_files'
        if mineru_dir.exists():
            ref_files = ReferenceFile.query.with_entities(ReferenceFile.markdown_content).filter(
                ReferenceFile.markdown_content.isnot(None)
            ).all()
            referenced_ids = set()
            for (content,) in ref_files:
                if content:
                    referenced_ids.update(re.findall(r'/files/mineru/([^/]+)/', content))

            for entry in sorted(mineru_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name not in referenced_ids:
                    size = dir_size(entry)
                    total_size += size
                    total_count += 1
                    print(f'  {entry.name}  {human_size(size)}')
                    if delete:
                        shutil.rmtree(entry)
                        print(f'    -> 已删除')

        # 4. 孤立用户模板
        print('\n=== 孤立用户模板 ===')
        tpl_dir = upload_folder / 'user-templates'
        if tpl_dir.exists():
            for entry in sorted(tpl_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if UserTemplate.query.filter_by(id=entry.name).first() is None:
                    size = dir_size(entry)
                    total_size += size
                    total_count += 1
                    print(f'  {entry.name}  {human_size(size)}')
                    if delete:
                        shutil.rmtree(entry)
                        print(f'    -> 已删除')

        # 5. 中间产物
        for dir_name in ('editable_images', 'tmp'):
            print(f'\n=== 中间产物: {dir_name} ===')
            target_dir = upload_folder / dir_name
            if not target_dir.exists():
                continue

            for entry in sorted(target_dir.iterdir()):
                size = dir_size(entry) if entry.is_dir() else entry.stat().st_size
                total_size += size
                total_count += 1
                print(f'  {dir_name}/{entry.name}  {human_size(size)}')
                if delete:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                    print(f'    -> 已删除')

        # 汇总
        print(f'\n{"=" * 40}')
        print(f'孤立项总数: {total_count}')
        print(f'占用空间: {human_size(total_size)}')
        if not delete:
            print(f'\n以上为 dry-run 结果，加 --delete 参数执行实际删除')
        else:
            print(f'\n已完成清理')


if __name__ == '__main__':
    main()
