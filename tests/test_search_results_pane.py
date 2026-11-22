"""Shared result navigation never activates a hidden or implicit target."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from attendance_lab.presentation.search_results import SearchResultsPane


def test_arrows_choose_visible_edges_and_enter_activates_only_explicit_visible_item():
    app = QApplication.instance() or QApplication([])
    pane = SearchResultsPane()
    pane.results.addItems(
        ["Скрытая первая", "Первая видимая", "Последняя видимая", "Скрытая последняя"]
    )
    pane.results.item(0).setHidden(True)
    pane.results.item(3).setHidden(True)
    activated = []
    pane.activation.connect(lambda: activated.append(pane.results.currentRow()))
    pane.show()
    app.processEvents()
    pane.focus_search()
    QTest.keyClick(pane.search, Qt.Key.Key_Return)
    assert activated == []
    QTest.keyClick(pane.search, Qt.Key.Key_Down)
    assert pane.results.currentRow() == 1 and pane.results.hasFocus()
    QTest.keyClick(pane.results, Qt.Key.Key_Return)
    assert activated == [1]
    pane.focussearch()
    QTest.keyClick(pane.search, Qt.Key.Key_Up)
    assert pane.results.currentRow() == 2
    pane.results.currentItem().setHidden(True)
    QTest.keyClick(pane.results, Qt.Key.Key_Return)
    assert activated == [1]
    pane.results.setCurrentRow(1)
    position = pane.results.visualItemRect(pane.results.item(1)).center()
    QTest.mouseClick(pane.results.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(pane.results.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert activated == [1, 1]
    pane.close()
