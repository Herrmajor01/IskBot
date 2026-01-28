#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as m


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate pretension docx from PDFs in a directory."
    )
    parser.add_argument(
        "--input-dir",
        default="isk_outputs",
        help="Directory with PDF documents.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output docx path. Default: isk_outputs/pretension_generated_from_dir_<timestamp>.docx",
    )
    parser.add_argument(
        "--use-vision",
        action="store_true",
        help="Enable Vision OCR for low-quality pages.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use PyMuPDF only (faster, less accurate).",
    )
    parser.add_argument(
        "--no-llm-fallback",
        action="store_true",
        help="Skip LLM fallback for missing fields.",
    )
    parser.add_argument(
        "--skip-sliding-window",
        action="store_true",
        help="Skip sliding window parsing (faster).",
    )
    parser.add_argument(
        "--no-document-groups-llm",
        action="store_true",
        help="Skip LLM document grouping.",
    )
    parser.add_argument(
        "--no-payment-llm",
        action="store_true",
        help="Skip LLM fallback for payment terms.",
    )
    parser.add_argument(
        "--no-transport-llm",
        action="store_true",
        help="Skip LLM fallback for transport details.",
    )
    parser.add_argument(
        "--skip-application-terms",
        action="store_true",
        help="Skip per-application payment terms extraction (faster).",
    )
    parser.add_argument(
        "--no-awareness-llm",
        action="store_true",
        help="Skip LLM in special cases analysis.",
    )
    return parser.parse_args()


def collect_pdfs(input_dir: Path) -> List[Path]:
    return sorted(
        path for path in input_dir.glob("*.pdf")
        if path.is_file()
    )


def extract_pages(
    path: Path,
    use_vision: bool,
    fast: bool
) -> Tuple[List[str], List[int]]:
    if fast:
        from pdf_extractor import extract_with_pymupdf

        results = extract_with_pymupdf(str(path))
        pages = [r.get("text", "") for r in results]
        low_pages = [
            r["page_num"] for r in results
            if r.get("text_quality", 1.0) < 0.6 or len(r.get("text", "")) < 40
        ]
    else:
        pages, low_pages = m.extract_pdf_pages(str(path))

    processed_low_pages: List[int] = []
    if use_vision and low_pages:
        processed = m.apply_vision_ocr_to_pages(
            str(path),
            pages,
            low_pages
        )
        if processed:
            processed_low_pages = processed
            joined = ", ".join(str(num) for num in processed)
            print(f"Vision OCR applied: {path.name} pages {joined}")

    if use_vision:
        config = m.get_vision_config()
        max_pages = int(config.get("max_pages") or 0)
        targeted_pages = m.collect_targeted_ocr_pages(pages, processed_low_pages)
        if targeted_pages:
            if max_pages > 0:
                remaining = max_pages - len(set(processed_low_pages))
                if remaining <= 0:
                    targeted_pages = []
                else:
                    targeted_pages = targeted_pages[:remaining]
            if targeted_pages:
                processed = m.apply_vision_ocr_to_pages(
                    str(path),
                    pages,
                    targeted_pages
                )
                if processed:
                    joined = ", ".join(str(num) for num in processed)
                    print(f"Vision OCR (targeted) applied: {path.name} pages {joined}")

        scan_limit_raw = os.getenv("VISION_DOC_SCAN_PAGES", "2")
        try:
            scan_limit = int(scan_limit_raw)
        except ValueError:
            scan_limit = 2
        if scan_limit > 0:
            vision_pages = m.collect_vision_doc_pages(
                pages,
                processed_low_pages,
                limit=scan_limit
            )
            if vision_pages:
                processed = m.apply_vision_document_extraction(
                    str(path),
                    pages,
                    vision_pages
                )
                if processed:
                    joined = ", ".join(str(num) for num in processed)
                    print(
                        f"Vision document scan applied: {path.name} pages {joined}"
                    )

    return pages, low_pages


def select_pages_by_filename(pages_by_file: dict, keywords: List[str]) -> List[str]:
    selected: List[str] = []
    if not keywords:
        return selected
    for name, pages in pages_by_file.items():
        lower = name.lower()
        if any(keyword in lower for keyword in keywords):
            selected.extend(pages)
    return selected


def build_combined_text(
    files: List[Path],
    use_vision: bool,
    fast: bool
) -> Tuple[str, List[str], List[Tuple[str, str, List[int]]], dict]:
    combined_texts = []
    all_pages: List[str] = []
    low_pages_info: List[Tuple[str, str, List[int]]] = []
    pages_by_file = {}

    for path in files:
        log(f"Extracting pages from {path.name}")
        pages, low_pages = extract_pages(path, use_vision, fast)
        all_pages.extend(pages)
        pages_by_file[path.name] = pages

        page_blocks = []
        for idx, page in enumerate(pages, start=1):
            page_blocks.append(f"[Страница {idx}]\n{page}")
        combined_texts.append(
            f"=== {path.name} ===\n" + "\n\n".join(page_blocks)
        )

        if low_pages:
            low_pages_info.append((str(path), path.name, low_pages))

    combined_text = "\n\n".join(combined_texts)
    return combined_text, all_pages, low_pages_info, pages_by_file


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}")
        return 1

    files = collect_pdfs(input_dir)
    if not files:
        print(f"No PDF files found in {input_dir}")
        return 1

    log(f"Found {len(files)} PDF files in {input_dir}")
    combined_text, all_pages, low_pages_info, pages_by_file = build_combined_text(
        files,
        args.use_vision,
        args.fast
    )
    log(f"Extracted {len(all_pages)} pages")

    if args.skip_sliding_window:
        claim_data = {}
    else:
        log("Parsing documents with sliding window")
        claim_data = m.parse_documents_with_sliding_window(combined_text)
        if not args.no_llm_fallback:
            log("Applying LLM fallback")
            claim_data = m.apply_llm_fallback(combined_text, claim_data)

    if args.no_document_groups_llm or args.skip_sliding_window:
        claim_data["document_groups"] = m.build_document_groups_from_data(
            claim_data
        )
    else:
        log("Building document groups with LLM")
        claim_data["document_groups"] = m.build_document_groups(
            combined_text,
            claim_data
        )
    claim_data["source_files"] = [path.name for path in files]

    app_pages = select_pages_by_filename(
        pages_by_file,
        ["заявк"]
    ) or all_pages
    invoice_pages = select_pages_by_filename(
        pages_by_file,
        ["счет", "счёт"]
    ) or all_pages
    upd_pages = select_pages_by_filename(
        pages_by_file,
        ["упд"]
    ) or all_pages
    cargo_pages = select_pages_by_filename(
        pages_by_file,
        ["сопровод"]
    ) or all_pages
    shipment_pages = select_pages_by_filename(
        pages_by_file,
        ["почтов", "чек", "отчет", "отчёт"]
    ) or all_pages

    log("Extracting applications")
    allow_transport_llm = not args.no_transport_llm
    applications = m.extract_applications_from_pages(
        app_pages,
        allow_llm=allow_transport_llm
    )
    log(f"Applications: {len(applications)}")

    log("Extracting invoices")
    invoices = m.extract_invoices_from_pages(invoice_pages)
    log(f"Invoices: {len(invoices)}")

    log("Extracting UPD")
    upd_docs = m.extract_upd_from_pages(upd_pages)
    log(f"UPD: {len(upd_docs)}")

    log("Extracting cargo docs")
    cargo_docs = m.extract_cargo_docs_from_pages(
        cargo_pages,
        allow_llm=allow_transport_llm
    )
    log(f"Cargo docs: {len(cargo_docs)}")

    if args.use_vision and low_pages_info:
        log("Enriching cargo docs with vision")
        cargo_docs = m.enrich_cargo_docs_with_vision(
            cargo_docs,
            [{"path": str(path), "name": path.name} for path in files],
            low_pages_info
        )

    log("Extracting shipments")
    shipments = m.extract_cdek_shipments_from_pages(shipment_pages)
    shipments.extend(m.extract_postal_shipments_from_pages(shipment_pages))

    # Обогащаем почтовые отправления через API Почты России (если настроено)
    config = m.get_russian_post_config()
    if shipments and config.get("enabled"):
        for shipment in shipments:
            if m.normalize_shipping_source(shipment.get("source")) != "post":
                continue
            track = shipment.get("track_number") or ""
            if not m.is_valid_tracking_number(track):
                continue
            try:
                records = m.fetch_russian_post_operations(track)
                shipment["api_records"] = len(records)
                send_date, receive_date = m.extract_tracking_dates(records)
                if receive_date:
                    shipment["received_date"] = m.parse_date_str(receive_date) or receive_date
                    shipment["received_date_str"] = receive_date
                if send_date:
                    shipment["send_date"] = send_date
            except Exception:
                continue

    log("Extracting payment terms")
    payment_terms_by_application = {}
    allow_payment_llm = not args.no_payment_llm
    if not args.skip_application_terms:
        payment_terms_by_application = m.extract_application_payment_terms(
            app_pages,
            applications,
            allow_llm=allow_payment_llm
        )
    groups = m.build_pretension_groups(
        applications,
        invoices,
        cargo_docs,
        upd_docs=upd_docs,
        payment_terms_by_application=payment_terms_by_application
    )
    m.assign_shipments_to_groups(groups, shipments)
    log(f"Built {len(groups)} pretension groups")

    payment_terms = None
    payment_days = None
    if payment_terms_by_application:
        terms_values = [
            m.normalize_payment_terms(item.get("terms") or "")
            for item in payment_terms_by_application.values()
            if item.get("terms")
        ]
        days_values = [
            item.get("days")
            for item in payment_terms_by_application.values()
            if item.get("days")
        ]
        unique_terms = {value for value in terms_values if value}
        unique_days = {value for value in days_values if value}
        if len(unique_terms) == 1:
            payment_terms = unique_terms.pop()
        if len(unique_days) == 1:
            payment_days = unique_days.pop()
    elif applications:
        payment_terms, payment_days = m.extract_payment_terms_from_text(
            "\n".join(app_pages),
            allow_llm=allow_payment_llm
        )
    if not payment_terms:
        payment_terms, payment_days = m.extract_payment_terms_from_text(
            combined_text,
            allow_llm=allow_payment_llm
        )

    if payment_terms:
        claim_data["payment_terms"] = payment_terms
    if payment_days:
        claim_data["payment_days"] = str(payment_days)

    parties = m.extract_parties_from_pages(all_pages)
    if parties:
        m.apply_extracted_parties(claim_data, parties)

    legal_docs = m.extract_legal_docs_from_pages(all_pages)
    if legal_docs:
        for key, value in legal_docs.items():
            if value and (m.is_missing_value(claim_data.get(key)) or key == "legal_fees"):
                claim_data[key] = value

    total_debt = sum(group.get("amount") or 0.0 for group in groups)
    if total_debt > 0:
        claim_data["debt"] = m.format_money(total_debt, 2)

    use_awareness_llm = not args.no_awareness_llm
    original_debt_decimal = Decimal(str(total_debt)) if total_debt > 0 else None
    awareness_result = m.analyze_documents_for_special_cases(
        all_pages,
        original_debt=original_debt_decimal,
        use_llm=use_awareness_llm
    )
    if (
        awareness_result.has_partial_payments
        or awareness_result.has_guarantee_letters
        or awareness_result.has_debt_acknowledgment
    ):
        claim_data = m.adjust_claim_data(claim_data, awareness_result)

    reconciliation_entries, reconciliation_sales = m.extract_reconciliation_entries(
        all_pages
    )
    reconciliation_payments = [
        entry for entry in reconciliation_entries
        if entry.get("entry_type") == "payment"
    ]
    if reconciliation_payments:
        allocated, unassigned = m.match_reconciliation_payments_to_groups(
            groups,
            reconciliation_payments,
            sales=reconciliation_sales
        )
        if allocated:
            reconciled = []
            allocated_total = 0.0
            for payment in allocated:
                date_value = payment.get("date")
                date_str = (
                    date_value.strftime("%d.%m.%Y")
                    if hasattr(date_value, "strftime") else str(date_value or "")
                )
                amount_value = float(payment.get("amount") or 0)
                allocated_total += amount_value
                reconciled.append({
                    "amount": str(amount_value),
                    "date": date_str,
                    "payment_number": payment.get("payment_number"),
                    "group_label": payment.get("group_label"),
                })
            if allocated_total > 0:
                adjusted_debt = max(total_debt - allocated_total, 0.0)
                claim_data["debt"] = m.format_money(adjusted_debt, 2)
            claim_data["partial_payments_info"] = reconciled
            claim_data["partial_payments_total"] = str(allocated_total)
        if not allocated:
            claim_data["partial_payments_info"] = []
            claim_data["partial_payments_total"] = "0"
            claim_data["debt"] = m.format_money(total_debt, 2)
        if unassigned:
            log(
                "Act reconciliation payments not matched strictly: "
                + ", ".join(
                    f"{p.get('date')}={p.get('amount')}" for p in unassigned[:5]
                )
            )

    if shipments:
        for shipment in shipments:
            shipment["source"] = m.normalize_shipping_source(
                shipment.get("source")
            )
        claim_data["shipments"] = shipments
        if any(item.get("source") == "post" for item in shipments):
            claim_data["shipping_method"] = "почта"
        elif any(item.get("source") == "cdek" for item in shipments):
            claim_data["shipping_method"] = "сдэк"
        shipments_for_numbers = shipments
        if any(item.get("api_records", 0) > 0 for item in shipments):
            shipments_for_numbers = [
                item for item in shipments if item.get("api_records", 0) > 0
            ]
        claim_data["postal_numbers"] = [
            item["track_number"] for item in shipments_for_numbers if item.get("track_number")
        ]
        postal_dates = []
        for item in shipments:
            received = item.get("received_date")
            if isinstance(received, str):
                parsed = m.parse_date_str(received)
                if parsed:
                    received = parsed
            if hasattr(received, "strftime"):
                postal_dates.append(received.strftime("%d.%m.%Y"))
        claim_data["postal_dates"] = postal_dates
        claim_data["docs_track_number"] = m.get_first_list_value(
            claim_data.get("postal_numbers", [])
        )
        claim_data["docs_received_date"] = m.get_first_list_value(
            claim_data.get("postal_dates", [])
        )

    claim_data["pretension_groups"] = groups

    raw_plaintiff_name = m.normalize_str(claim_data.get("plaintiff_name"))
    raw_defendant_name = m.normalize_str(claim_data.get("defendant_name"))
    plaintiff_name_short = m.format_organization_name_short(raw_plaintiff_name)
    defendant_name_short = m.format_organization_name_short(raw_defendant_name)
    plaintiff_name = plaintiff_name_short
    defendant_name = defendant_name_short
    is_plaintiff_ip = (
        "ИП" in plaintiff_name or "Индивидуальный предприниматель" in plaintiff_name
    )
    is_defendant_ip = (
        "ИП" in defendant_name or "Индивидуальный предприниматель" in defendant_name
    )

    debt_amount = m.parse_amount(claim_data.get("debt", "0"))
    debt_decimal = m.parse_amount_decimal(claim_data.get("debt", "0"))
    debt_rubles, debt_kopeks = m.split_rubles_kopeks(debt_decimal)

    payment_days_raw = claim_data.get("payment_days", "0")
    try:
        payment_days_val = int(re.sub(r"[^\d]", "", str(payment_days_raw)))
    except ValueError:
        payment_days_val = 0

    interest_data = {"total_interest": 0.0, "detailed_calc": []}
    has_group_payment_days = False
    if groups:
        for group in groups:
            try:
                if int(group.get("payment_days") or 0) > 0:
                    has_group_payment_days = True
                    break
            except (TypeError, ValueError):
                continue

    partial_payments = claim_data.get("partial_payments_info") or []
    has_group_terms = any(
        m.normalize_payment_terms(group.get("payment_terms") or "")
        for group in (groups or [])
    )
    if groups and (payment_days_val > 0 or has_group_payment_days or has_group_terms):
        interest_data = m.calculate_pretension_interest_schedule(
            groups,
            payment_days_val,
            payments=partial_payments
        )
    else:
        docs_received_date = m.parse_date_str(
            claim_data.get("docs_received_date", "")
        )
        if docs_received_date and payment_days_val > 0 and debt_amount > 0:
            calendar = m.load_work_calendar(docs_received_date.year)
            due_date = m.add_working_days(
                docs_received_date,
                payment_days_val,
                calendar
            )
            interest_start = due_date + timedelta(days=1)
            interest_data = m.calculate_pretension_interest(
                debt_amount,
                interest_start,
                payments=partial_payments
            )

    total_interest = m.parse_amount(interest_data.get("total_interest", 0))
    legal_fees_value = m.parse_amount(claim_data.get("legal_fees", "0"))

    payment_terms_text = m.normalize_payment_terms(
        claim_data.get("payment_terms", "")
    )
    if not payment_terms_text or payment_terms_text == "Не указано":
        if payment_days_val > 0:
            payment_terms_text = (
                "Оплата не позднее "
                f"{payment_days_val} рабочих дней с даты получения документов, "
                "подтверждающих перевозку"
            )
        else:
            payment_terms_text = "Не указано"

    payment_terms_text = m.build_payment_terms_summary(
        groups,
        payment_terms_text,
        payment_days_val
    )

    applications_list = [
        group.get("application")
        for group in (groups or claim_data.get("document_groups", []))
        if group.get("application")
    ]
    cargo_docs_list = []
    for group in groups:
        cargo_docs_list.extend(group.get("cargo_docs", []))
    if not cargo_docs_list:
        cargo_docs_list = m.split_document_items(claim_data.get("cargo_docs"))
    intro_paragraph = m.build_intro_paragraph(
        plaintiff_name_short,
        applications_list,
        cargo_docs_list,
        include_docs=False
    )

    plaintiff_ogrn_type = m.get_ogrn_label(
        plaintiff_name,
        claim_data.get("plaintiff_inn", "")
    )
    defendant_ogrn_type = m.get_ogrn_label(
        defendant_name,
        claim_data.get("defendant_inn", "")
    )

    defendant_block = m.build_party_block(
        "Кому",
        defendant_name_short,
        m.normalize_str(claim_data.get("defendant_inn")),
        m.normalize_str(claim_data.get("defendant_kpp")),
        m.normalize_str(claim_data.get("defendant_ogrn")),
        defendant_ogrn_type,
        m.normalize_str(claim_data.get("defendant_address")),
        m.normalize_str(claim_data.get("defendant_address")),
        is_defendant_ip
    )
    plaintiff_block = m.build_party_block(
        "От кого",
        plaintiff_name_short,
        m.normalize_str(claim_data.get("plaintiff_inn")),
        m.normalize_str(claim_data.get("plaintiff_kpp")),
        m.normalize_str(claim_data.get("plaintiff_ogrn")),
        plaintiff_ogrn_type,
        m.normalize_str(claim_data.get("plaintiff_address")),
        m.normalize_str(claim_data.get("plaintiff_address")),
        is_plaintiff_ip
    )

    if groups:
        documents_list_structured = m.build_documents_list_structured_for_groups(
            groups
        )
    else:
        documents_list_structured = m.build_documents_list_structured(
            claim_data.get("document_groups", [])
        )

    attachments = m.build_pretension_attachments(
        groups or claim_data.get("document_groups", []),
        claim_data
    )
    shipping_summary = m.build_shipping_summary(
        shipments,
        documents_count=len(groups) if groups else None
    )

    replacements = {
        "{defendant_block}": defendant_block,
        "{plaintiff_block}": plaintiff_block,
        "{intro_paragraph}": intro_paragraph,
        "{documents_list}": m.build_documents_list(claim_data),
        "{debt_amount}": debt_rubles,
        "{debt_kopeks}": debt_kopeks,
        "{payment_terms}": payment_terms_text,
        "{legal_fees_block}": m.build_legal_fees_block(claim_data),
        "{requirements_summary}": m.build_requirements_summary(
            debt_amount,
            total_interest,
            legal_fees_value
        ),
        "{pretension_date}": m.format_russian_date(),
        "{shipping_info}": shipping_summary,
        "{docs_track_number}": m.normalize_str(
            claim_data.get("docs_track_number", ""),
            default=""
        ),
        "{docs_received_date}": m.normalize_str(
            claim_data.get("docs_received_date", ""),
            default=""
        ),
        "{plaintiff_name}": plaintiff_name,
        "{defendant_name}": defendant_name,
    }

    awareness_text = claim_data.get("awareness_text", "")
    if awareness_text:
        adjusted_debt_str = claim_data.get(
            "debt",
            m.format_money(debt_amount, 2)
        )
        awareness_text = awareness_text.replace(
            "{adjusted_debt}",
            adjusted_debt_str
        )
        replacements["{awareness_block}"] = awareness_text
    else:
        replacements["{awareness_block}"] = ""

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("isk_outputs")
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"pretension_generated_from_dir_{stamp}.docx"

    result_docx = m.create_pretension_document(
        claim_data,
        interest_data,
        replacements,
        documents_list_structured=documents_list_structured,
        attachments=attachments,
        output_path=str(output_path),
        proofread_protected_values=[
            plaintiff_name,
            defendant_name,
            plaintiff_name_short,
            defendant_name_short,
        ],
    )

    log(f"Generated: {result_docx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
