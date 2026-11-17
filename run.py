"""Lançador unificado para iniciar a API FastAPI e o Dashboard Streamlit juntos."""

import socket
import subprocess
import sys
import time


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Verifica se uma porta TCP local já está em uso."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> None:
    """Sobe FastAPI (8000) se necessário e Streamlit (8501), exibindo links locais."""
    processes: list[subprocess.Popen] = []

    try:
        if is_port_in_use(8000):
            print("[OK] API FastAPI já está rodando na porta 8000.")
        else:
            api_proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.api:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ]
            )
            processes.append(api_proc)
            print("[INICIADA] API FastAPI (Webhook) na porta 8000.")

        if is_port_in_use(8501):
            print("[OK] Dashboard Streamlit já está rodando na porta 8501.")
        else:
            dash_proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    "dashboard.py",
                    "--server.port",
                    "8501",
                    "--server.headless",
                    "true",
                ]
            )
            processes.append(dash_proc)
            print("[INICIADO] Dashboard Streamlit na porta 8501.")

        print("\n=== Links de Acesso ===")
        print("Dashboard (Streamlit): http://localhost:8501")
        print("API Docs (Swagger):    http://localhost:8000/docs")
        print("Pressione Ctrl+C para encerrar os processos iniciados por este script.\n")

        if not processes:
            return

        while all(proc.poll() is None for proc in processes):
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nEncerrando serviços...")
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    main()
