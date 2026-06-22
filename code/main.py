from core import IntegratedSpatioTemporalApp

if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    win = IntegratedSpatioTemporalApp() 
    win.show()
    sys.exit(app.exec())