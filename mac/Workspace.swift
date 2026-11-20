import SwiftUI
import AppKit

enum Style {
    static let teal = Color(red:0.035,green:0.38,blue:0.42)
    static let mint = Color(red:0.32,green:0.81,blue:0.84)
    static func accent(_ scheme: ColorScheme) -> Color { scheme == .dark ? mint : teal }
    static func paper(_ scheme: ColorScheme) -> Color { scheme == .dark ? Color(white:0.128) : Color(red:0.982,green:0.979,blue:0.965) }
}
struct Primary: ButtonStyle {
    @Environment(\.colorScheme) var scheme
    @Environment(\.isEnabled) var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size:13,weight:.semibold)).padding(.horizontal,15).padding(.vertical,9)
            .foregroundColor(enabled ? (scheme == .dark ? .black : .white) : .secondary)
            .background(enabled ? Style.accent(scheme).opacity(configuration.isPressed ? 0.85 : 1) : Color.secondary.opacity(0.14))
            .cornerRadius(10)
    }
}

struct Workspace: View {
    @ObservedObject var m: Model
    @Environment(\.colorScheme) var scheme
    @Environment(\.accessibilityReduceTransparency) var solid
    @State private var confirm = false
    @State private var fullStatus = false
    let pages = ["Прошивка","Диагностика","Дамп устройства"]
    let icons = ["arrow.down.circle","waveform.path.ecg","externaldrive"]
    var busy: Bool { !m.activity.isEmpty }
    var accent: Color { Style.accent(scheme) }
    var body: some View {
        GeometryReader { geo in
            HStack(spacing:0) {
                if geo.size.width >= 710 {
                    VStack(alignment:.leading,spacing:4) {
                        Text("XREAL Updater").font(.headline).padding(.leading,8).padding(.bottom,14)
                        ForEach(pages.indices,id:\.self) { i in
                            Button { m.section = pages[i] } label: {
                                HStack { Image(systemName:icons[i]).frame(width:21); Text(pages[i]).fixedSize(horizontal:true,vertical:false); Spacer(minLength:0) }.padding(8).contentShape(Rectangle())
                            }.buttonStyle(.plain).foregroundColor(m.section == pages[i] ? accent : .primary)
                                .background(m.section == pages[i] ? accent.opacity(0.12) : Color.clear).cornerRadius(8)
                                .accessibilityAddTraits(m.section == pages[i] ? .isSelected : [])
                        }
                        Spacer()
                        Text("Версия 0.7.2").font(.caption).foregroundColor(.secondary).padding(.leading,8)
                    }.padding(12).frame(width:196).background { if solid { Color(nsColor:.windowBackgroundColor) } else { Rectangle().fill(.regularMaterial) } }
                    Divider()
                }
                VStack(alignment:.leading,spacing:0) {
                    if geo.size.width < 710 {
                        Picker("Раздел",selection:$m.section) { ForEach(pages,id:\.self){ Text($0).tag($0) } }.pickerStyle(.menu).padding(.horizontal,16).padding(.vertical,8)
                        Divider()
                    }
                    ScrollView {
                        VStack(alignment:.leading,spacing:16) {
                            VStack(alignment:.leading,spacing:4) {
                                Text(m.section == pages[0] ? "Установка прошивки" : m.section).font(.system(size:26,weight:.semibold))
                                Text(["Выберите очки и пакет обновления Pilot.","Проверьте подключение, версии и совместимость BIN.","Сохраните доступные файлы и идентификаторы очков."][pages.firstIndex(of:m.section) ?? 0]).foregroundColor(.secondary)
                            }
                            deviceRow
                            Divider()
                            if m.section == pages[0] { firmware }
                            else if m.section == pages[1] { diagnostic }
                            else { dump }
                        }.padding(geo.size.width < 710 ? 16 : 24).frame(maxWidth:.infinity,alignment:.leading)
                    }
                    Divider()
                    VStack(alignment:.leading,spacing:8) {
                        if !m.status.isEmpty && (busy || m.statusSection == m.section) {
                            HStack(alignment:.firstTextBaseline) {
                                Text(m.status).lineLimit(3).textSelection(.enabled)
                                    .foregroundColor(m.failure ? (scheme == .dark ? Color(red:1,green:0.65,blue:0.6) : Color(red:0.65,green:0.18,blue:0.14)) : .primary)
                                if m.status.count > 85 {
                                    Button("Подробнее"){fullStatus = true}
                                        .popover(isPresented:$fullStatus) {
                                            ScrollView { Text(m.status).textSelection(.enabled).padding() }.frame(width:320,height:180)
                                        }
                                }
                            }
                        }
                        if busy { if let value = m.percent { ProgressView(value:value,total:100) } else { ProgressView().progressViewStyle(.linear) } }
                        HStack { footer }
                    }.padding(.horizontal,16).padding(.vertical,12)
                }.background(Style.paper(scheme))
            }
        }.accentColor(accent)
        .alert("Установить выбранную прошивку?",isPresented:$confirm) {
            Button("Отмена",role:.cancel) {}
            Button("Установить") { if m.ready { m.run("update",message:"Проверка перед записью…") } }
        } message:{Text("\(m.current?.label ?? "")\n\(m.bin?.lastPathComponent ?? "")\nОставьте USB-кабель подключённым до завершения проверки.")}
    }
    var deviceRow: some View {
        VStack(alignment:.leading,spacing:8) {
            Text("Устройство").font(.headline)
            HStack {
                Image(systemName:"eyeglasses").font(.title2).foregroundColor(accent).accessibilityHidden(true)
                VStack(alignment:.leading,spacing:3) {
                    Text(m.current?.label ?? (m.devices.isEmpty ? "Очки не найдены" : "Выберите очки")).fontWeight(.medium)
                    Text(m.current == nil ? "Подключите USB-кабель с передачей данных." : (m.identity["sku"].map{"SKU " + $0} ?? "USB HID")).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                }
                Spacer()
                if !m.devices.isEmpty {
                    Menu("Выбрать") { ForEach(Array(m.devices.enumerated()),id:\.element.id) { i,d in Button("\(d.label) · №\(i+1)"){m.select(d)} } }.fixedSize().disabled(busy)
                }
                Button { m.refresh() } label:{Image(systemName:"arrow.clockwise")}.help("Обновить список").accessibilityLabel("Обновить список").keyboardShortcut("r").disabled(busy)
            }
            if let soc = m.identity["soc"], let pilot = m.identity["pilot"] {
                Text("SoC \(soc)  ·  Pilot \(pilot)").font(.caption).foregroundColor(.secondary).textSelection(.enabled)
            }
            if m.current != nil && !m.known && !busy && m.section != pages[2] {
                Button("Перейти к сбору дампа"){m.section = pages[2]}
            }
        }
    }
    var firmware: some View {
        VStack(alignment:.leading,spacing:9) {
            Text("Файл прошивки").font(.headline)
            HStack {
                Image(systemName:"doc").font(.title2).foregroundColor(accent)
                VStack(alignment:.leading,spacing:3) {
                    Text(m.package.isEmpty ? "BIN не выбран" : m.package).fontWeight(.medium)
                    Text(m.bin?.lastPathComponent ?? "Нужен пакет для вашей модели.").font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                }
                Spacer()
                Button("Выбрать BIN…"){m.choose()}.disabled(busy).keyboardShortcut("o")
            }
            if m.bin != nil { Label("Содержимое BIN проверено",systemImage:"checkmark.circle").foregroundColor(accent).font(.caption) }
            Label("Закройте страницу обновления XREAL. Оставьте очки подключёнными до завершения проверки файлов.",systemImage:"cable.connector").foregroundColor(.secondary).font(.callout).padding(.top,12)
        }
    }
    var diagnostic: some View {
        VStack(alignment:.leading,spacing:8) {
            Text("Отчёт").font(.headline)
            if m.report.isEmpty { Text("Нажмите «Проверить сейчас». Для проверки подключения BIN необязателен.").foregroundColor(.secondary) }
            else { Text(m.report).font(.system(.caption,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }
        }
    }
    var dump: some View {
        VStack(alignment:.leading,spacing:8) {
            Text("Содержимое дампа").font(.headline)
            Text("Версии SoC, DSP и Pilot, исходные байты SKU и файлы, доступные через протокол экспорта XREAL.")
            Text("Можно выбрать новый PID или неизвестный SKU. В папке появятся report.json, архивы и извлечённые файлы. Полнота зависит от устройства. Дамп может содержать идентификаторы и настройки.").foregroundColor(.secondary)
            if let folder = m.folder { Button("Показать папку в Finder"){ NSWorkspace.shared.activateFileViewerSelecting([folder]) } }
        }
    }
    @ViewBuilder var footer: some View {
        if m.section == pages[0] {
            Button("Проверить без записи"){m.run("verify",message:"Проверка установленных файлов…")}.disabled(!m.ready)
            Spacer()
            Button("Установить прошивку"){confirm=true}.buttonStyle(Primary()).disabled(!m.ready)
        } else if m.section == pages[1] {
            Menu("Отчёт") {
                Button("Скопировать"){let p = NSPasteboard.general;p.clearContents();p.setString(m.report,forType:.string)}
                Button("Сохранить TXT…"){m.saveReport()}
            }.fixedSize().disabled(m.report.isEmpty || busy)
            Spacer()
            Button("Проверить сейчас"){m.diagnostic()}.buttonStyle(Primary()).disabled(busy)
        } else {
            if m.activity == "dump" {Button("Остановить"){m.stop()}.disabled(!m.canStop)}
            Spacer()
            Button("Сохранить дамп…"){m.dump()}.buttonStyle(Primary()).disabled(busy || m.current == nil)
        }
    }
}
