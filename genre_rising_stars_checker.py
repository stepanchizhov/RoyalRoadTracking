document.addEventListener("DOMContentLoaded", function() {
    document.getElementById("risingStarsForm").addEventListener("submit", function(event) {
        event.preventDefault();

        let bookUrl = document.getElementById("book_url").value;
        let apiUrl = "https://royalroadtracking.onrender.com/check_rising_stars";  
        let baseRisingStarsUrl = "https://www.royalroad.com/fictions/rising-stars?genre=";  

        // Extract the book's Royal Road ID from the URL
        let bookIdMatch = bookUrl.match(/fiction\/(\d+)/);
        let bookId = bookIdMatch ? bookIdMatch[1] : null;

        // Show fetching animation & hide previous results
        document.getElementById("fetchingData").style.display = "block";
        document.getElementById("risingStarsResults").style.display = "none";
        document.getElementById("copyResults").style.display = "none";
        document.getElementById("risingStarsResults").innerHTML = "";

        fetch(apiUrl + "?book_url=" + encodeURIComponent(bookUrl))
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP Error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            document.getElementById("fetchingData").style.display = "none"; 
            document.getElementById("risingStarsResults").style.display = "block";
            document.getElementById("copyResults").style.display = "block";

            let resultHTML = "<h3>Results:</h3><ul>";
            for (const [tag, status] of Object.entries(data)) {
                let tagUrl = baseRisingStarsUrl + encodeURIComponent(tag);
                
                if (bookId) {
                    tagUrl += `#fiction-${bookId}`;  // Append the book's unique ID as an anchor
                }

                let resultText = status;

                if (status.includes("✅ Found in position #")) {
                    let position = status.match(/#(\d+)/)[1];  
                    resultText = `✅ <a href="${tagUrl}" target="_blank" style="color: #0073e6; text-decoration: none;">Found in position #${position}</a>`;
                }

                resultHTML += `<li><strong>${tag}:</strong> <span id="tag-${tag}">Processing...</span></li>`;
            }
            resultHTML += "</ul>";
            document.getElementById("risingStarsResults").innerHTML = resultHTML;

            // **Display results one by one with a 3-second delay**
            let index = 0;
            for (const [tag, status] of Object.entries(data)) {
                setTimeout(() => {
                    let tagUrl = baseRisingStarsUrl + encodeURIComponent(tag);
                    
                    if (bookId) {
                        tagUrl += `#fiction-${bookId}`; // Add the book's ID
                    }

                    let resultText = status;

                    if (status.includes("✅ Found in position #")) {
                        let position = status.match(/#(\d+)/)[1];  
                        resultText = `✅ Found in <a href="${tagUrl}" target="_blank" style="color: #0073e6; text-decoration: none;">position #${position}</a>`;
                    }

                    document.getElementById(`tag-${tag}`).innerHTML = resultText;
                }, 1500 * index);
                index++;
            }
        })
        .catch(error => {
            document.getElementById("fetchingData").style.display = "none"; 
            document.getElementById("risingStarsResults").style.display = "block";
            document.getElementById("risingStarsResults").innerHTML = `<p style="color:red;">Error fetching results: ${error}</p>`;
        });
    });

    // Copy results function
    document.getElementById("copyResults").addEventListener("click", function() {
        let text = document.getElementById("risingStarsResults").innerText;
        navigator.clipboard.writeText(text);
        alert("Copied to clipboard! 📋");
    });
});
