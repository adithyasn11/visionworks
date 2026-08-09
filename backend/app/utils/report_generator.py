# backend/app/utils/report_generator.py
import os
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

class AnalyticsReportGenerator:
    """
    Generates executive CSV and PDF reports summarizing physical workplace activity telemetry,
    posture health ratios, and workstation utilization metrics.
    """
    @staticmethod
    def generate_csv_report(data_logs: list, output_filepath: str) -> str:
        """Exports raw activity telemetry logs to CSV format"""
        df = pd.DataFrame(data_logs)
        df.to_csv(output_filepath, index=False)
        return output_filepath

    @staticmethod
    def generate_pdf_report(summary_stats: dict, output_filepath: str) -> str:
        """Generates an executive PDF report summarizing workplace utilization"""
        doc = SimpleDocTemplate(output_filepath, pagesize=letter)
        story = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Heading1'],
            fontSize=20,
            textColor=colors.HexColor('#0F172A'),
            spaceAfter=12
        )

        subtitle_style = ParagraphStyle(
            'ReportSubtitle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#475569'),
            spaceAfter=20
        )

        story.append(Paragraph("Vision-Based Workplace Activity Analytics Report", title_style))
        story.append(Paragraph("Executive Summary • Physical Space & Ergonomic Utilization Telemetry", subtitle_style))
        story.append(Spacer(1, 10))

        # Metrics Summary Table
        table_data = [
            ["Metric Name", "Observed Value"],
            ["Total Telemetry Logs Recorded", str(summary_stats.get("total_logs", 0))],
            ["Average Activity Score Index", f"{summary_stats.get('average_activity_score', 0)} / 100"],
            ["Sitting Posture Ratio", f"{summary_stats.get('posture_distribution', {}).get('sitting_percentage', 0)}%"],
            ["Standing Posture Ratio", f"{summary_stats.get('posture_distribution', {}).get('standing_percentage', 0)}%"],
            ["Walking / Movement Ratio", f"{summary_stats.get('posture_distribution', {}).get('walking_percentage', 0)}%"]
        ]

        t = Table(table_data, colWidths=[240, 200])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#FFFFFF')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F8FAFC')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ]))

        story.append(t)
        doc.build(story)
        return output_filepath
