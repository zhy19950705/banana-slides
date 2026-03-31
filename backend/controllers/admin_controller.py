"""
Admin Controller - 管理端接口，包括孤立文件清理
"""
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Blueprint, request, current_app

from models import Project, ReferenceFile, UserTemplate
from utils import success_response, error_response, require_allowed_client_id_or_error

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')


def _get_upload_folder() -> Path:
    return Path(current_app.config['UPLOAD_FOLDER'])


def _calculate_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def _get_latest_mtime(path: Path) -> float:
    latest_mtime = path.stat().st_mtime
    if path.is_dir():
        for entry in path.rglob('*'):
            try:
                latest_mtime = max(latest_mtime, entry.stat().st_mtime)
            except FileNotFoundError:
                continue
    return latest_mtime


def _build_cleanup_item(path: Path, relative_path: str) -> dict:
    size = _calculate_size(path)
    modified_at = datetime.fromtimestamp(_get_latest_mtime(path), timezone.utc)
    return {
        'path': relative_path,
        'size_bytes': size,
        'size_human': _human_size(size),
        'modified_at': modified_at.isoformat(),
    }


def _should_include_path(path: Path, cutoff_time: datetime | None) -> bool:
    if cutoff_time is None:
        return True
    modified_at = datetime.fromtimestamp(_get_latest_mtime(path), timezone.utc)
    return modified_at <= cutoff_time


def _parse_older_than_days(raw_value: str | None) -> int | None:
    if raw_value in (None, ''):
        return None

    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError('older_than_days must be an integer')

    if value < 0:
        raise ValueError('older_than_days must be >= 0')

    return value


def _get_cutoff_time(older_than_days: int | None) -> datetime | None:
    if older_than_days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=older_than_days)


def _find_orphan_project_dirs(upload_folder: Path, cutoff_time: datetime | None = None) -> list[dict]:
    """找出 uploads/ 下不被 projects 表引用的 UUID 目录"""
    # 排除已知的非项目目录
    known_dirs = {'materials', 'mineru_files', 'reference_files', 'user-templates', 'editable_images', 'tmp'}
    orphans = []

    for entry in upload_folder.iterdir():
        if not entry.is_dir() or entry.name in known_dirs:
            continue
        # 检查是否为有效项目
        project = Project.query.filter_by(id=entry.name).first()
        if project is None and _should_include_path(entry, cutoff_time):
            orphans.append(_build_cleanup_item(entry, str(entry.relative_to(upload_folder))))

    return orphans


def _find_orphan_reference_files(upload_folder: Path, cutoff_time: datetime | None = None) -> list[dict]:
    """找出 reference_files/ 下不被数据库引用的文件"""
    ref_dir = upload_folder / 'reference_files'
    if not ref_dir.exists():
        return []

    # 获取所有数据库中记录的文件路径
    db_paths = {r.file_path for r in ReferenceFile.query.with_entities(ReferenceFile.file_path).all()}

    orphans = []
    for entry in ref_dir.iterdir():
        if not entry.is_file():
            continue
        relative = f'reference_files/{entry.name}'
        if relative not in db_paths and _should_include_path(entry, cutoff_time):
            orphans.append(_build_cleanup_item(entry, relative))

    return orphans


def _find_orphan_mineru_dirs(upload_folder: Path, cutoff_time: datetime | None = None) -> list[dict]:
    """找出 mineru_files/ 下未被任何 ReferenceFile.markdown_content 引用的目录"""
    mineru_dir = upload_folder / 'mineru_files'
    if not mineru_dir.exists():
        return []

    # 收集所有 markdown_content 中引用的 extract_id
    ref_files = ReferenceFile.query.with_entities(ReferenceFile.markdown_content).filter(
        ReferenceFile.markdown_content.isnot(None)
    ).all()
    referenced_ids = set()
    for (content,) in ref_files:
        if content:
            # markdown_content 中包含 /files/mineru/{extract_id}/ 格式的路径
            import re
            ids = re.findall(r'/files/mineru/([^/]+)/', content)
            referenced_ids.update(ids)

    orphans = []
    for entry in mineru_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name not in referenced_ids and _should_include_path(entry, cutoff_time):
            orphans.append(_build_cleanup_item(entry, f'mineru_files/{entry.name}'))

    return orphans


def _find_orphan_user_templates(upload_folder: Path, cutoff_time: datetime | None = None) -> list[dict]:
    """找出 user-templates/ 下不被数据库引用的目录"""
    tpl_dir = upload_folder / 'user-templates'
    if not tpl_dir.exists():
        return []

    orphans = []
    for entry in tpl_dir.iterdir():
        if not entry.is_dir():
            continue
        template = UserTemplate.query.filter_by(id=entry.name).first()
        if template is None and _should_include_path(entry, cutoff_time):
            orphans.append(_build_cleanup_item(entry, f'user-templates/{entry.name}'))

    return orphans


def _find_expired_export_files(upload_folder: Path, cutoff_time: datetime | None = None) -> list[dict]:
    """找出 uploads/<project>/exports/ 下超过保留期的导出文件"""
    if cutoff_time is None:
        return []

    known_dirs = {'materials', 'mineru_files', 'reference_files', 'user-templates', 'editable_images', 'tmp'}
    expired = []

    for project_dir in upload_folder.iterdir():
        if not project_dir.is_dir() or project_dir.name in known_dirs:
            continue

        exports_dir = project_dir / 'exports'
        if not exports_dir.exists():
            continue

        for entry in exports_dir.iterdir():
            if entry.is_file() and _should_include_path(entry, cutoff_time):
                expired.append(_build_cleanup_item(entry, str(entry.relative_to(upload_folder))))

    return expired


def _find_expired_children(
    upload_folder: Path,
    relative_dir: str,
    cutoff_time: datetime | None = None
) -> list[dict]:
    """找出 uploads/<relative_dir>/ 下超过保留期的中间产物。"""
    if cutoff_time is None:
        return []

    target_dir = upload_folder / relative_dir
    if not target_dir.exists():
        return []

    expired = []
    for entry in target_dir.iterdir():
        if _should_include_path(entry, cutoff_time):
            expired.append(_build_cleanup_item(entry, str(entry.relative_to(upload_folder))))

    return expired


def _human_size(size_bytes: int) -> str:
    """将字节数转换为人类可读格式"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} TB'


def build_cleanup_report(
    upload_folder: Path,
    *,
    older_than_days: int | None = None,
    include_orphans: bool = True,
    include_exports: bool = False,
    include_intermediate: bool = True,
) -> dict:
    cutoff_time = _get_cutoff_time(older_than_days)

    orphan_projects = _find_orphan_project_dirs(upload_folder, cutoff_time) if include_orphans else []
    orphan_refs = _find_orphan_reference_files(upload_folder, cutoff_time) if include_orphans else []
    orphan_mineru = _find_orphan_mineru_dirs(upload_folder, cutoff_time) if include_orphans else []
    orphan_templates = _find_orphan_user_templates(upload_folder, cutoff_time) if include_orphans else []
    expired_exports = _find_expired_export_files(upload_folder, cutoff_time) if include_exports else []
    expired_editable_images = _find_expired_children(upload_folder, 'editable_images', cutoff_time) if include_intermediate else []
    expired_tmp = _find_expired_children(upload_folder, 'tmp', cutoff_time) if include_intermediate else []

    orphans = {
        'projects': orphan_projects,
        'reference_files': orphan_refs,
        'mineru_files': orphan_mineru,
        'user_templates': orphan_templates,
    }
    expired = {
        'exports': expired_exports,
        'editable_images': expired_editable_images,
        'tmp': expired_tmp,
    }

    all_categories = list(orphans.values()) + list(expired.values())
    total_size = sum(item['size_bytes'] for category in all_categories for item in category)
    total_count = sum(len(category) for category in all_categories)

    return {
        'older_than_days': older_than_days,
        'cutoff_time': cutoff_time.isoformat() if cutoff_time else None,
        'orphans': orphans,
        'expired': expired,
        'summary': {
            'total_count': total_count,
            'total_size_bytes': total_size,
            'total_size_human': _human_size(total_size),
        },
    }


def execute_cleanup(
    *,
    dry_run: bool,
    older_than_days: int | None = None,
    include_orphans: bool = True,
    include_exports: bool = False,
    include_intermediate: bool = True,
) -> dict:
    upload_folder = _get_upload_folder()
    report = build_cleanup_report(
        upload_folder,
        older_than_days=older_than_days,
        include_orphans=include_orphans,
        include_exports=include_exports,
        include_intermediate=include_intermediate,
    )

    deleted = []
    failed = []
    if not dry_run:
        for category_group in (report['orphans'], report['expired']):
            for category_items in category_group.values():
                for item in category_items:
                    target = upload_folder / item['path']
                    try:
                        if target.is_dir():
                            shutil.rmtree(target)
                        elif target.is_file():
                            target.unlink()
                        deleted.append(item['path'])
                        logger.info(f'Deleted cleanup target: {item["path"]} ({item["size_human"]})')
                    except Exception as exc:
                        failed.append({'path': item['path'], 'error': str(exc)})
                        logger.error(f'Failed to delete {item["path"]}: {exc}')

    report['summary']['deleted_count'] = len(deleted) if not dry_run else 0
    report['summary']['failed_count'] = len(failed)
    report['deleted'] = deleted if not dry_run else []
    report['failed'] = failed
    report['dry_run'] = dry_run
    report['include_orphans'] = include_orphans
    report['include_exports'] = include_exports
    report['include_intermediate'] = include_intermediate

    return report


@admin_bp.route('/cleanup', methods=['POST'])
def cleanup_orphan_files():
    """
    POST /api/admin/cleanup

    清理孤立的上传文件。

    Query params:
      - dry_run: "true" (默认) 仅列出孤立文件，"false" 执行删除
      - older_than_days: 可选，仅清理超过 N 天未修改的目标
      - include_exports: "true" 时同时清理过期导出文件
      - include_orphans: "true" (默认) 时清理孤立文件
      - include_intermediate: "true" (默认) 时清理 editable_images/tmp 中的过期中间产物

    Returns:
      各类别的孤立文件列表及总大小
    """
    _, access_error = require_allowed_client_id_or_error(
        current_app.config.get('SETTINGS_ALLOWED_CLIENT_IDS', ()),
        feature_name='Admin cleanup',
    )
    if access_error:
        return access_error

    dry_run = request.args.get('dry_run', 'true').lower() != 'false'
    include_exports = request.args.get('include_exports', 'false').lower() == 'true'
    include_orphans = request.args.get('include_orphans', 'true').lower() != 'false'
    include_intermediate = request.args.get('include_intermediate', 'true').lower() != 'false'
    upload_folder = _get_upload_folder()

    if not upload_folder.exists():
        return error_response('NOT_FOUND', 'Upload folder does not exist', 404)

    try:
        older_than_days = _parse_older_than_days(request.args.get('older_than_days'))
    except ValueError as exc:
        return error_response('INVALID_REQUEST', str(exc), 400)

    report = execute_cleanup(
        dry_run=dry_run,
        older_than_days=older_than_days,
        include_orphans=include_orphans,
        include_exports=include_exports,
        include_intermediate=include_intermediate,
    )

    if dry_run:
        message = 'Cleanup dry run completed'
    elif include_orphans and not include_exports and not include_intermediate:
        message = f'Deleted {report["summary"]["deleted_count"]} orphan items'
    else:
        message = f'Deleted {report["summary"]["deleted_count"]} cleanup items'

    return success_response(
        data=report,
        message=message,
    )
