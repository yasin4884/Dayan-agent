import PyPDF2
from config import CHUNK_SIZE, CHUNK_OVERLAP


class PDFProcessor:
    def read_pdf(self, pdf_path: str) -> str:
        text = ""
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
            print(f"✅ متن استخراج شد: {len(text)} کاراکتر")
        except Exception as e:
            print(f"❌ خطا در خواندن PDF: {e}")
        return text

    def chunk_text(self, text: str) -> list:
        if not text.strip():
            print("⚠️ متن خالی است")
            return []
        
        words = text.split()
        chunks = []
        step = CHUNK_SIZE - CHUNK_OVERLAP
        
        for i in range(0, len(words), step):
            chunk = ' '.join(words[i:i + CHUNK_SIZE])
            if chunk.strip():
                chunks.append({"text": chunk, "source": ""})
        
        print(f"✅ {len(chunks)} chunk ساخته شد")
        return chunks
