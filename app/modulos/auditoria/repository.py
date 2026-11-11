# app/modulos/auditoria/repository.py
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

ZONA = ZoneInfo("America/Guayaquil")
COLUMNAS = """id_evento, ocurrido_en, modulo, accion, nivel, resultado, entidad, id_entidad,
              id_entidad_padre, id_usuario, email, rol, origen, host(ip) AS ip, user_agent,
              id_solicitud, resumen, cambios, datos"""


def _inicio(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=ZONA)


class AuditoriaRepository:
    def __init__(self, db: Session):
        self.db = db

    def buscar_eventos(self, desde: date, hasta: date, *, modulo: Optional[str] = None,
                       accion: Optional[str] = None, email: Optional[str] = None,
                       entidad: Optional[str] = None, id_entidad: Optional[str] = None,
                       nivel: Optional[str] = None, resultado: Optional[str] = None,
                       origen: Optional[str] = None, texto: Optional[str] = None,
                       limite: int = 100, desplazamiento: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        filtros = ["ocurrido_en >= :desde", "ocurrido_en < :hasta"]
        params: Dict[str, Any] = {"desde": _inicio(desde), "hasta": _inicio(hasta + timedelta(days=1)),
                                  "limite": limite, "desplazamiento": desplazamiento}
        for campo, valor in (("modulo", modulo), ("accion", accion), ("entidad", entidad),
                             ("id_entidad", id_entidad), ("nivel", nivel), ("resultado", resultado),
                             ("origen", origen)):
            if valor:
                filtros.append(f"{campo} = :{campo}")
                params[campo] = valor
        if email:
            filtros.append("email ILIKE :email")
            params["email"] = f"%{email.strip()}%"
        if texto:
            filtros.append("resumen ILIKE :texto")
            params["texto"] = f"%{texto.strip()}%"
        donde = " AND ".join(filtros)
        total = self.db.execute(text(f"SELECT count(*) FROM auditoria.eventos WHERE {donde}"), params).scalar()
        filas = self.db.execute(text(
            f"SELECT {COLUMNAS} FROM auditoria.eventos WHERE {donde} "
            "ORDER BY ocurrido_en DESC, id_evento DESC LIMIT :limite OFFSET :desplazamiento"), params).mappings().all()
        return [dict(f) for f in filas], int(total or 0)

    def obtener_evento(self, id_evento: int) -> Optional[Dict[str, Any]]:
        fila = self.db.execute(text(f"SELECT {COLUMNAS} FROM auditoria.eventos WHERE id_evento = :id"),
                               {"id": id_evento}).mappings().first()
        return dict(fila) if fila else None

    def historial(self, entidad: str, id_entidad: str, limite: int = 500) -> List[Dict[str, Any]]:
        """Eventos del registro y de sus hijos (id_entidad_padre), del más reciente al más antiguo."""
        filas = self.db.execute(text(
            f"SELECT {COLUMNAS} FROM auditoria.eventos "
            "WHERE (entidad = :entidad AND id_entidad = :id) OR id_entidad_padre = :id "
            "ORDER BY ocurrido_en DESC, id_evento DESC LIMIT :limite"),
            {"entidad": entidad, "id": id_entidad, "limite": limite}).mappings().all()
        return [dict(f) for f in filas]

    def listar_acciones(self) -> List[Dict[str, Any]]:
        filas = self.db.execute(text(
            "SELECT codigo, modulo, descripcion, nivel, activo FROM auditoria.acciones ORDER BY codigo")).mappings()
        return [dict(f) for f in filas]

    def verificar_cadena(self) -> Dict[str, Any]:
        return dict(self.db.execute(text("SELECT * FROM auditoria.verificar_cadena()")).mappings().one())
