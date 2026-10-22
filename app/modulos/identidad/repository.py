from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import text

class IdentidadRepository:
    def __init__(self, db: Session):
        self.db = db

    def obtener_usuario_por_email(self, email: str) -> Optional[Dict[str, Any]]:
        sql = text("""
            SELECT id_usuario, email, nombre, rol, activo
            FROM identidad.usuarios
            WHERE LOWER(email) = LOWER(:email);
        """)
        res = self.db.execute(sql, {"email": email.strip()}).fetchone()
        return dict(res._mapping) if res else None

    def obtener_usuario_por_id(self, id_usuario: UUID) -> Optional[Dict[str, Any]]:
        sql = text("""
            SELECT id_usuario, email, nombre, rol, activo
            FROM identidad.usuarios
            WHERE id_usuario = :id_usuario;
        """)
        res = self.db.execute(sql, {"id_usuario": id_usuario}).fetchone()
        return dict(res._mapping) if res else None

    def listar_usuarios(self) -> List[Dict[str, Any]]:
        sql = text("""
            SELECT id_usuario, email, nombre, rol, activo, created_at
            FROM identidad.usuarios
            ORDER BY created_at DESC;
        """)
        res = self.db.execute(sql).fetchall()
        return [dict(row._mapping) for row in res]

    def listar_destinatarios(self) -> List[Dict[str, Any]]:
        sql = text("""
            SELECT id_usuario, email, nombre, rol
            FROM identidad.usuarios
            WHERE activo = true
            ORDER BY nombre;
        """)
        return [dict(row._mapping) for row in self.db.execute(sql).fetchall()]

    def crear_usuario(self, email: str, nombre: str, rol: str) -> Dict[str, Any]:
        sql = text("""
            INSERT INTO identidad.usuarios (email, nombre, rol, activo)
            VALUES (LOWER(:email), :nombre, :rol, true)
            RETURNING id_usuario, email, nombre, rol, activo, created_at;
        """)
        res = self.db.execute(sql, {"email": email.strip(), "nombre": nombre, "rol": rol}).fetchone()
        self.db.commit()
        return dict(res._mapping)

    def actualizar_estado(self, id_usuario: UUID, activo: bool) -> Optional[Dict[str, Any]]:
        sql = text("""
            UPDATE identidad.usuarios
            SET activo = :activo, updated_at = CURRENT_TIMESTAMP
            WHERE id_usuario = :id_usuario
            RETURNING id_usuario, email, nombre, rol, activo;
        """)
        res = self.db.execute(sql, {"id_usuario": id_usuario, "activo": activo}).fetchone()
        self.db.commit()
        return dict(res._mapping) if res else None

    def actualizar_usuario(self, id_usuario: UUID, cambios: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Actualiza solo las columnas indicadas (email, nombre, rol, activo)."""
        permitidas = {"email", "nombre", "rol", "activo"}
        cambios = {k: v for k, v in cambios.items() if k in permitidas}
        if not cambios:
            return self.obtener_usuario_por_id(id_usuario)
        sets = ", ".join(f"{k} = :{k}" for k in cambios)
        sql = text(f"""
            UPDATE identidad.usuarios
            SET {sets}, updated_at = CURRENT_TIMESTAMP
            WHERE id_usuario = :id_usuario
            RETURNING id_usuario, email, nombre, rol, activo, created_at;
        """)
        res = self.db.execute(sql, {**cambios, "id_usuario": id_usuario}).fetchone()
        self.db.commit()
        return dict(res._mapping) if res else None

    def actualizar_rol(self, id_usuario: UUID, rol: str) -> Optional[Dict[str, Any]]:
        sql = text("""
            UPDATE identidad.usuarios
            SET rol = :rol, updated_at = CURRENT_TIMESTAMP
            WHERE id_usuario = :id_usuario
            RETURNING id_usuario, email, nombre, rol, activo;
        """)
        res = self.db.execute(sql, {"id_usuario": id_usuario, "rol": rol}).fetchone()
        self.db.commit()
        return dict(res._mapping) if res else None