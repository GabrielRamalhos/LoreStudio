from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget)
from app.services.ai import validate_pack


class AIPanel(QWidget):
    def __init__(self, button, generate, save):
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QLabel('Planejamento com IA local')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        notice = QLabel('Revise o roteiro na aba Roteiro antes de gerar. Aqui são criados textos e prompts, não imagens. Nada é publicado automaticamente.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 30)
        self.count.setValue(5)
        self.style = QLineEdit('cinematográfico sombrio, paleta consistente')
        form.addRow('Quantidade de cenas', self.count)
        form.addRow('Estilo visual', self.style)
        layout.addLayout(form)
        self.notice = QLabel('Nenhum planejamento gerado.')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        tabs = QTabWidget()
        self.scenes = QTableWidget(0, 2)
        self.scenes.setHorizontalHeaderLabels(['Trecho exato da narração', 'Prompt visual editável'])
        self.scenes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scenes.verticalHeader().setDefaultSectionSize(90)
        tabs.addTab(self.scenes, 'Cenas e prompts')
        page = QWidget()
        fields = QFormLayout(page)
        self.titles = [QLineEdit() for _ in range(3)]
        for index, title in enumerate(self.titles, 1):
            fields.addRow(f'Título {index}', title)
        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(120)
        self.hashtags = QLineEdit()
        self.tags = QLineEdit()
        self.thumbnail = QPlainTextEdit()
        self.thumbnail.setMaximumHeight(80)
        self.cta = QLineEdit()
        for title, widget in [('Descrição', self.description), ('Hashtags (separadas por vírgula)', self.hashtags),
                              ('Tags (separadas por vírgula)', self.tags), ('Prompt de thumbnail', self.thumbnail), ('Chamada para ação', self.cta)]:
            fields.addRow(title, widget)
        tabs.addTab(page, 'Publicação — rascunho')
        layout.addWidget(tabs)
        row = QHBoxLayout()
        button(row, 'Gerar cenas e metadados', generate).setObjectName('primary')
        button(row, 'Salvar revisão', save)
        layout.addLayout(row)
        self.source_script = ''
        self.current_script = ''
        self.scenes.itemChanged.connect(lambda: self.update_notice(self.current_script))
        for field in [*self.titles, self.description, self.hashtags, self.tags, self.thumbnail, self.cta]:
            field.textChanged.connect(lambda: self.update_notice(self.current_script))

    def pack(self):
        def cell(row, column):
            item = self.scenes.item(row, column)
            return item.text() if item else ''
        return dict(scenes=[dict(index=row + 1, text=cell(row, 0), prompt=cell(row, 1)) for row in range(self.scenes.rowCount())],
                    titles=[field.text() for field in self.titles], description=self.description.toPlainText(),
                    hashtags=[item.strip() for item in self.hashtags.text().split(',') if item.strip()],
                    tags=[item.strip() for item in self.tags.text().split(',') if item.strip()],
                    thumbnail_prompt=self.thumbnail.toPlainText(), cta=self.cta.text())

    def set_pack(self, pack, source_script=''):
        self.source_script = source_script
        scenes = pack.get('scenes', [])
        self.scenes.setRowCount(len(scenes))
        for row, scene in enumerate(scenes):
            self.scenes.setItem(row, 0, QTableWidgetItem(scene.get('text', '')))
            self.scenes.setItem(row, 1, QTableWidgetItem(scene.get('prompt', '')))
        titles = pack.get('titles', [])
        for index, field in enumerate(self.titles):
            field.setText(titles[index] if index < len(titles) else '')
        self.description.setPlainText(pack.get('description', ''))
        self.hashtags.setText(', '.join(pack.get('hashtags', [])))
        self.tags.setText(', '.join(pack.get('tags', [])))
        self.thumbnail.setPlainText(pack.get('thumbnail_prompt', ''))
        self.cta.setText(pack.get('cta', ''))

    def update_notice(self, script):
        self.current_script = script
        if not self.scenes.rowCount():
            self.notice.setText('Nenhum planejamento gerado.')
        elif script != self.source_script:
            self.notice.setText('Roteiro alterado: o planejamento anterior foi preservado, mas precisa ser atualizado.')
        else:
            try:
                validate_pack(self.pack(), script)
            except ValueError as error:
                self.notice.setText(f'Rascunho incompleto: {error} O salvamento normal preserva suas edições, sem aprová-las.')
            else:
                self.notice.setText('Rascunho de IA: revise fatos, divisão das cenas e metadados. Use Salvar revisão após editar.')
